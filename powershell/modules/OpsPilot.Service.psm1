# OpsPilot.Service — native Windows Service lifecycle.
# Every mutating function supports -WhatIf and uses approved verbs.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-OpsPilotServiceState {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Name
    )

    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $service) {
        return [pscustomobject]@{
            exists         = $false
            name           = $Name
            status         = $null
            binary_path    = $null
            service_account = $null
        }
    }

    $escaped = $Name.Replace('\', '\\').Replace("'", "''")
    $cim = Get-CimInstance -ClassName Win32_Service -Filter "Name='$escaped'" -ErrorAction SilentlyContinue
    $binaryPath = $null
    $account = $null
    if ($null -ne $cim) {
        $binaryPath = [string]$cim.PathName
        $account = [string]$cim.StartName
    }

    return [pscustomobject]@{
        exists          = $true
        name            = $service.Name
        status          = $service.Status.ToString()
        binary_path     = $binaryPath
        service_account = $account
    }
}

function Wait-OpsPilotServiceStatus {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Expected,
        [int]$TimeoutSeconds = 45
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
        if ($null -eq $service) {
            throw "Service '$Name' disappeared while waiting for '$Expected'."
        }
        if ($service.Status.ToString() -eq $Expected) {
            return $service.Status.ToString()
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    throw "Service '$Name' did not reach '$Expected' within $TimeoutSeconds seconds."
}

function Install-OpsPilotService {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$PackagePath,
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Version,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$ServiceAccount,
        [string]$InstallRoot = 'C:\OpsPilot\apps'
    )

    if (-not (Test-Path -LiteralPath $PackagePath -PathType Container)) {
        throw "Package directory not found: $PackagePath"
    }
    $sourceExecutable = Join-Path -Path $PackagePath -ChildPath $Executable
    if (-not (Test-Path -LiteralPath $sourceExecutable -PathType Leaf)) {
        throw "Executable not found in package: $sourceExecutable"
    }

    $targetPath = Join-Path -Path (Join-Path -Path $InstallRoot -ChildPath $Name) -ChildPath $Version
    $targetExecutable = Join-Path -Path $targetPath -ChildPath $Executable

    $argumentLine = ($Arguments | ForEach-Object {
        if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
    }) -join ' '
    $binaryPathName = '"' + $targetExecutable + '"'
    if ($argumentLine) {
        $binaryPathName = $binaryPathName + ' ' + $argumentLine
    }

    if (-not $PSCmdlet.ShouldProcess($Name, "Install version $Version from $PackagePath")) {
        return [pscustomobject]@{
            service_name = $Name
            status       = 'WhatIf'
            install_path = $targetPath
            version      = $Version
        }
    }

    New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
    Copy-Item -Path (Join-Path -Path $PackagePath -ChildPath '*') -Destination $targetPath -Recurse -Force

    $existing = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if ($existing.Status.ToString() -ne 'Stopped') {
            Stop-Service -Name $Name -Force -ErrorAction Stop
            Wait-OpsPilotServiceStatus -Name $Name -Expected 'Stopped' | Out-Null
        }
        & sc.exe config $Name 'binPath=' $binaryPathName 'start=' 'auto' 'obj=' $ServiceAccount | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "sc.exe config failed for '$Name' with exit code $LASTEXITCODE."
        }
    }
    else {
        New-Service -Name $Name -BinaryPathName $binaryPathName -DisplayName $Name -StartupType Automatic | Out-Null
        & sc.exe config $Name 'start=' 'auto' 'obj=' $ServiceAccount | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "sc.exe config failed for new service '$Name' with exit code $LASTEXITCODE."
        }
    }

    Start-Service -Name $Name -ErrorAction Stop
    $status = Wait-OpsPilotServiceStatus -Name $Name -Expected 'Running'

    $installed = Get-OpsPilotServiceState -Name $Name
    if (-not $installed.exists) {
        throw "Service '$Name' disappeared after installation."
    }
    if ($installed.binary_path -notlike "*$targetExecutable*") {
        throw "Service '$Name' is running from '$($installed.binary_path)' but expected '$targetExecutable'."
    }
    $fileVersion = (Get-Item -LiteralPath $targetExecutable).VersionInfo.FileVersion
    if (-not [string]::IsNullOrWhiteSpace($fileVersion) -and $fileVersion -ne $Version) {
        throw "Installed executable reports version '$fileVersion' but deployment declared '$Version'."
    }

    return [pscustomobject]@{
        service_name = $Name
        status       = $status
        install_path = $targetPath
        version      = $Version
        binary_path  = $installed.binary_path
        file_version = $fileVersion
    }
}

function Restart-OpsPilotService {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Name
    )

    if (-not $PSCmdlet.ShouldProcess($Name, 'Restart Windows Service')) {
        return [pscustomobject]@{ service_name = $Name; status = 'WhatIf' }
    }

    Restart-Service -Name $Name -Force -ErrorAction Stop
    $status = Wait-OpsPilotServiceStatus -Name $Name -Expected 'Running'
    return [pscustomobject]@{ service_name = $Name; status = $status }
}

function Remove-OpsPilotService {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Name,
        [string]$InstallRoot = 'C:\OpsPilot\apps',
        [switch]$RemoveFiles
    )

    $existing = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        return [pscustomobject]@{ removed = $false; files_removed = $false }
    }

    if (-not $PSCmdlet.ShouldProcess($Name, 'Stop and delete Windows Service')) {
        return [pscustomobject]@{ removed = $false; files_removed = $false }
    }

    if ($existing.Status.ToString() -ne 'Stopped') {
        Stop-Service -Name $Name -Force -ErrorAction Stop
        Wait-OpsPilotServiceStatus -Name $Name -Expected 'Stopped' | Out-Null
    }

    & sc.exe delete $Name | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "sc.exe delete failed for '$Name' with exit code $LASTEXITCODE."
    }

    $deadline = (Get-Date).AddSeconds(30)
    do {
        $still = Get-Service -Name $Name -ErrorAction SilentlyContinue
        if ($null -eq $still) { break }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    $filesRemoved = $false
    if ($RemoveFiles.IsPresent) {
        $appPath = Join-Path -Path $InstallRoot -ChildPath $Name
        if (Test-Path -LiteralPath $appPath -PathType Container) {
            Remove-Item -LiteralPath $appPath -Recurse -Force
            $filesRemoved = $true
        }
    }

    return [pscustomobject]@{ removed = $true; files_removed = $filesRemoved }
}

function Restore-OpsPilotServiceState {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$StateBase64
    )

    $json = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($StateBase64))
    $state = $json | ConvertFrom-Json
    $name = [string]$state.name

    if (-not $PSCmdlet.ShouldProcess($name, 'Restore checkpointed Windows Service state')) {
        return Get-OpsPilotServiceState -Name $name
    }

    $existing = Get-Service -Name $name -ErrorAction SilentlyContinue
    if (-not [bool]$state.exists) {
        if ($null -ne $existing) {
            if ($existing.Status.ToString() -ne 'Stopped') {
                Stop-Service -Name $name -Force -ErrorAction Stop
                Wait-OpsPilotServiceStatus -Name $name -Expected 'Stopped' | Out-Null
            }
            & sc.exe delete $name | Out-Null
        }
        return Get-OpsPilotServiceState -Name $name
    }

    if ([string]::IsNullOrWhiteSpace([string]$state.binary_path)) {
        throw "Checkpoint for service '$name' has no binary path; cannot restore."
    }

    if ($null -eq $existing) {
        New-Service -Name $name -BinaryPathName ([string]$state.binary_path) -DisplayName $name -StartupType Automatic | Out-Null
    }
    else {
        if ($existing.Status.ToString() -ne 'Stopped') {
            Stop-Service -Name $name -Force -ErrorAction Stop
            Wait-OpsPilotServiceStatus -Name $name -Expected 'Stopped' | Out-Null
        }
        & sc.exe config $name 'binPath=' ([string]$state.binary_path) | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "sc.exe config failed while restoring '$name'."
        }
    }

    if (-not [string]::IsNullOrWhiteSpace([string]$state.service_account)) {
        & sc.exe config $name 'obj=' ([string]$state.service_account) | Out-Null
    }

    if ([string]$state.status -eq 'Running') {
        Start-Service -Name $name -ErrorAction Stop
        Wait-OpsPilotServiceStatus -Name $name -Expected 'Running' | Out-Null
    }

    return Get-OpsPilotServiceState -Name $name
}

Export-ModuleMember -Function Get-OpsPilotServiceState, Install-OpsPilotService, Restart-OpsPilotService, Remove-OpsPilotService, Restore-OpsPilotServiceState
