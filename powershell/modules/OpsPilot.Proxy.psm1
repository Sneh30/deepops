# OpsPilot.Proxy — Caddy configuration validation, reload, and checkpoint restore.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-OpsPilotCaddyState {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [string]$ServiceName = 'caddy'
    )

    $content = $null
    if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
        $content = [System.Convert]::ToBase64String(
            [System.Text.Encoding]::UTF8.GetBytes((Get-Content -LiteralPath $ConfigPath -Raw))
        )
    }

    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    $status = $null
    if ($null -ne $service) { $status = $service.Status.ToString() }

    return [pscustomobject]@{
        config_path     = $ConfigPath
        service_name    = $ServiceName
        content_base64  = $content
        service_exists  = ($null -ne $service)
        service_status  = $status
    }
}

function Test-OpsPilotCaddyConfig {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [string]$ServiceName = 'caddy'
    )

    $caddy = Get-Command -Name 'caddy' -ErrorAction Stop
    $validation = & $caddy.Source validate --config $ConfigPath --adapter caddyfile 2>&1
    $valid = ($LASTEXITCODE -eq 0)
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    $running = $null -ne $service -and $service.Status.ToString() -eq 'Running'

    return [pscustomobject]@{
        valid           = $valid
        service_running = $running
        output          = (($validation | Out-String).Trim())
    }
}

function Set-OpsPilotCaddyConfig {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][string]$ContentBase64,
        [string]$ServiceName = 'caddy'
    )

    $json = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($ContentBase64))
    $payload = $json | ConvertFrom-Json
    $content = [string]$payload.content
    if ([string]::IsNullOrWhiteSpace($content)) {
        throw 'Caddyfile content is empty.'
    }

    if (-not $PSCmdlet.ShouldProcess($ConfigPath, 'Write, validate, and reload Caddy configuration')) {
        return [pscustomobject]@{
            config_path     = $ConfigPath
            validated       = $false
            service_running = $false
        }
    }

    $directory = Split-Path -Path $ConfigPath -Parent
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $staging = "$ConfigPath.new"
    Set-Content -LiteralPath $staging -Value $content -Encoding utf8 -NoNewline

    $caddy = Get-Command -Name 'caddy' -ErrorAction Stop
    & $caddy.Source validate --config $staging --adapter caddyfile 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $staging -Force -ErrorAction SilentlyContinue
        throw 'Caddy rejected the generated configuration; existing config was left untouched.'
    }

    Move-Item -LiteralPath $staging -Destination $ConfigPath -Force
    Restart-Service -Name $ServiceName -Force -ErrorAction Stop

    $deadline = (Get-Date).AddSeconds(30)
    do {
        $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($null -ne $service -and $service.Status.ToString() -eq 'Running') { break }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    $test = Test-OpsPilotCaddyConfig -ConfigPath $ConfigPath -ServiceName $ServiceName
    return [pscustomobject]@{
        config_path     = $ConfigPath
        validated       = [bool]$test.valid
        service_running = [bool]$test.service_running
    }
}

function Restore-OpsPilotCaddyState {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$StateBase64
    )

    $json = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($StateBase64))
    $state = $json | ConvertFrom-Json
    $configPath = [string]$state.config_path
    $serviceName = [string]$state.service_name

    if (-not $PSCmdlet.ShouldProcess($configPath, 'Restore checkpointed Caddy configuration')) {
        return Get-OpsPilotCaddyState -ConfigPath $configPath -ServiceName $serviceName
    }

    if ([string]::IsNullOrWhiteSpace([string]$state.content_base64)) {
        Remove-Item -LiteralPath $configPath -Force -ErrorAction SilentlyContinue
    }
    else {
        $bytes = [System.Convert]::FromBase64String([string]$state.content_base64)
        $content = [System.Text.Encoding]::UTF8.GetString($bytes)
        $directory = Split-Path -Path $configPath -Parent
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
        Set-Content -LiteralPath $configPath -Value $content -Encoding utf8 -NoNewline
    }

    if ([bool]$state.service_exists) {
        Restart-Service -Name $serviceName -Force -ErrorAction Stop
    }

    return Get-OpsPilotCaddyState -ConfigPath $configPath -ServiceName $serviceName
}

Export-ModuleMember -Function Get-OpsPilotCaddyState, Test-OpsPilotCaddyConfig, Set-OpsPilotCaddyConfig, Restore-OpsPilotCaddyState
