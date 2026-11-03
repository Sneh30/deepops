# OpsPilot.Container — Windows-container deployment with fail-closed OS checks.
# This module never invokes WSL or a Linux container runtime.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-OpsPilotDockerOsType {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    $osType = (& docker info --format '{{.OSType}}' 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'docker info failed; Docker Engine must be installed and running.'
    }
    return $osType
}

function Assert-OpsPilotWindowsContainerMode {
    [CmdletBinding()]
    param()
    $osType = Get-OpsPilotDockerOsType
    if ($osType -ne 'windows') {
        throw "Docker is in '$osType' container mode. OpsPilot MVP requires Windows-container mode; Linux-container fallback is forbidden (ADR-003)."
    }
}

function Get-OpsPilotContainerState {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Name
    )

    Assert-OpsPilotWindowsContainerMode
    $inspect = & docker inspect $Name 2>$null
    if ($LASTEXITCODE -ne 0) {
        return [pscustomobject]@{
            exists         = $false
            name           = $Name
            status         = $null
            image          = $null
            os             = $null
            host_port      = $null
            container_port = $null
        }
    }

    $data = ($inspect | Out-String) | ConvertFrom-Json
    if ($null -eq $data -or $data.Count -eq 0) {
        return [pscustomobject]@{
            exists         = $false
            name           = $Name
            status         = $null
            image          = $null
            os             = $null
            host_port      = $null
            container_port = $null
        }
    }
    $container = $data[0]
    $image = [string]$container.Config.Image
    $imageOs = (& docker image inspect $image --format '{{.Os}}' 2>$null | Out-String).Trim()
    $hostPort = $null
    $containerPort = $null
    $bindings = $container.HostConfig.PortBindings
    if ($null -ne $bindings) {
        $first = $bindings.PSObject.Properties | Select-Object -First 1
        if ($null -ne $first) {
            $containerPort = [int]($first.Name -replace '/.*$', '')
            if ($first.Value.Count -gt 0) {
                $hostPort = [int]$first.Value[0].HostPort
            }
        }
    }

    return [pscustomobject]@{
        exists         = $true
        name           = $Name
        status         = [string]$container.State.Status
        image          = $image
        os             = $imageOs
        host_port      = $hostPort
        container_port = $containerPort
    }
}

function Install-OpsPilotContainer {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Image,
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, 65535)]
        [int]$HostPort,
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, 65535)]
        [int]$ContainerPort
    )

    Assert-OpsPilotWindowsContainerMode

    if (-not $PSCmdlet.ShouldProcess($Name, "Run Windows container $Image on $HostPort:$ContainerPort")) {
        return [pscustomobject]@{
            container_name = $Name
            image          = $Image
            status         = 'WhatIf'
            host_port      = $HostPort
        }
    }

    $imageOs = (& docker image inspect $Image --format '{{.Os}}' 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        & docker pull $Image | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to pull image $Image" }
        $imageOs = (& docker image inspect $Image --format '{{.Os}}' 2>$null | Out-String).Trim()
    }
    if ($imageOs -ne 'windows') {
        throw "Image '$Image' reports OS '$imageOs'. Only Windows-container images are permitted (ADR-003)."
    }

    & docker inspect $Name 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & docker rm -f $Name | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to replace existing container $Name" }
    }

    & docker run -d --name $Name -p "${HostPort}:${ContainerPort}" $Image | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "docker run failed for $Image"
    }

    $deadline = (Get-Date).AddSeconds(60)
    do {
        $state = Get-OpsPilotContainerState -Name $Name
        if ($state.exists -and $state.status -eq 'running') { break }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    return Get-OpsPilotContainerState -Name $Name
}

function Remove-OpsPilotContainer {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Name
    )

    $state = Get-OpsPilotContainerState -Name $Name
    if (-not $state.exists) {
        return [pscustomobject]@{ removed = $false; name = $Name }
    }
    if (-not $PSCmdlet.ShouldProcess($Name, 'Remove Windows container')) {
        return [pscustomobject]@{ removed = $false; name = $Name }
    }
    & docker rm -f $Name | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to remove container $Name" }
    return [pscustomobject]@{ removed = $true; name = $Name }
}

function Restore-OpsPilotContainerState {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$StateBase64
    )

    $json = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($StateBase64))
    $state = $json | ConvertFrom-Json
    $name = [string]$state.name

    if (-not $PSCmdlet.ShouldProcess($name, 'Restore checkpointed container state')) {
        return Get-OpsPilotContainerState -Name $name
    }

    Assert-OpsPilotWindowsContainerMode
    & docker inspect $name 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & docker rm -f $name | Out-Null
    }

    if ([bool]$state.exists) {
        & docker run -d --name $name -p "$($state.host_port):$($state.container_port)" ([string]$state.image) | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to restore container $name"
        }
    }

    return Get-OpsPilotContainerState -Name $name
}

Export-ModuleMember -Function Get-OpsPilotContainerState, Install-OpsPilotContainer, Remove-OpsPilotContainer, Restore-OpsPilotContainerState
