<#
.SYNOPSIS
    Installs the pinned OpsPilot observability stack on Windows (IMPL-301).

.DESCRIPTION
    Installs windows_exporter (MSI), Prometheus (zip), Grafana (zip), and NSSM
    (zip) from exact-version URLs and verifies every archive's SHA256 before it
    is extracted or executed (ENGINEERING_REFERENCE.md §2, IMPLEMENTATION.md §3.3).

    Prometheus and Grafana have no native Windows Service shim, so NSSM is used
    as the documented fallback supervisor (SAS.md §6). Both services bind to
    127.0.0.1 only. windows_exporter binds on TCP 9182 and is restricted by a
    named firewall rule to the configured scrape CIDRs.

    All files live under C:\ProgramData\OpsPilot by default. No step invokes
    bash, WSL, or a Linux container runtime.

.PARAMETER DataRoot
    Root for configuration, data, logs, downloads, and extracted binaries.

.PARAMETER MetricsScrapeCidrs
    Remote networks allowed to scrape windows_exporter on TCP 9182.

.PARAMETER GrafanaAdminPassword
    Optional Grafana admin password. When omitted, an existing configured
    password is preserved or a new random one is generated and written to an
    ACL-restricted file.

.PARAMETER WhatIf
    Print the exact pinned plan without downloading, extracting, or installing.
#>
#Requires -Version 7.2
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$DataRoot = 'C:\ProgramData\OpsPilot',

    [ValidateNotNullOrEmpty()]
    [string[]]$MetricsScrapeCidrs = @('127.0.0.1/32'),

    [securestring]$GrafanaAdminPassword,

    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $IsWindows) {
    throw 'install_stack.ps1 is Windows-only. Authoring and linting happen on Mac; installation happens on the Windows host.'
}

$script:Packages = [ordered]@{
    windows_exporter = [pscustomobject]@{
        Version = '0.32.1'
        Kind    = 'msi'
        Url     = 'https://github.com/prometheus-community/windows_exporter/releases/download/v0.32.1/windows_exporter-0.32.1-amd64.msi'
        Sha256  = '8e1ade6a2fbcdeb4774c300b598667cd7888246be8fcc4b586d11b3680433981'
    }
    prometheus       = [pscustomobject]@{
        Version = '3.15.1'
        Kind    = 'zip'
        Url     = 'https://github.com/prometheus/prometheus/releases/download/v3.15.1/prometheus-3.15.1.windows-amd64.zip'
        Sha256  = 'ba25b5e590520cb9fb028edef99bdc8523244d2d489b8c629e0d453d94fb030b'
    }
    grafana          = [pscustomobject]@{
        Version = '13.3.0'
        Kind    = 'zip'
        Url     = 'https://dl.grafana.com/oss/release/grafana-13.3.0.windows-amd64.zip'
        Sha256  = '133f0a0c7b905a6c7e4f3df596cf4df535ca78f1e3ef7e5be5f3f2178a51f75d'
    }
    nssm             = [pscustomobject]@{
        Version = '2.24'
        Kind    = 'zip'
        Url     = 'https://nssm.cc/release/nssm-2.24.zip'
        Sha256  = '727d1e42275c605e0f04aba98095c38a8e1e46def453cdffce42869428aa6743'
    }
}

$StackRoot = Join-Path -Path $DataRoot -ChildPath 'stack'
$script:MetricsScrapeCidrs = @($MetricsScrapeCidrs)
$DownloadRoot = Join-Path -Path $DataRoot -ChildPath 'downloads'
$LogRoot = Join-Path -Path $DataRoot -ChildPath 'logs'
$PrometheusRoot = Join-Path -Path $DataRoot -ChildPath 'prometheus'
$PrometheusData = Join-Path -Path $PrometheusRoot -ChildPath 'data'
$PrometheusConfig = Join-Path -Path $PrometheusRoot -ChildPath 'prometheus.yml'
$GrafanaRoot = Join-Path -Path $DataRoot -ChildPath 'grafana'
$GrafanaData = Join-Path -Path $GrafanaRoot -ChildPath 'data'
$GrafanaLogs = Join-Path -Path $GrafanaRoot -ChildPath 'logs'
$GrafanaProvisioning = Join-Path -Path $GrafanaRoot -ChildPath 'provisioning'
$GrafanaDashboards = Join-Path -Path $GrafanaRoot -ChildPath 'dashboards'
$GrafanaConfig = Join-Path -Path $GrafanaRoot -ChildPath 'grafana.ini'
$GrafanaPasswordFile = Join-Path -Path $GrafanaRoot -ChildPath 'admin-password.txt'

$PrometheusRootVersion = Join-Path -Path $StackRoot -ChildPath ('prometheus\{0}' -f $script:Packages.prometheus.Version)
$PrometheusExe = Join-Path -Path $PrometheusRootVersion -ChildPath 'prometheus.exe'
$GrafanaRootVersion = Join-Path -Path $StackRoot -ChildPath ('grafana\{0}' -f $script:Packages.grafana.Version)
$GrafanaExe = Join-Path -Path $GrafanaRootVersion -ChildPath 'bin\grafana.exe'
$NssmRootVersion = Join-Path -Path $StackRoot -ChildPath ('nssm\{0}' -f $script:Packages.nssm.Version)
$NssmExe = Join-Path -Path $NssmRootVersion -ChildPath 'win64\nssm.exe'
$StackManifest = Join-Path -Path $StackRoot -ChildPath 'stack-manifest.json'

$PrometheusService = 'OpsPilotPrometheus'
$GrafanaService = 'OpsPilotGrafana'
$ExporterService = 'windows_exporter'
$MetricsPort = 9182
$PrometheusPort = 9090
$GrafanaPort = 3000
$PrometheusUrl = 'http://127.0.0.1:{0}' -f $PrometheusPort
$GrafanaUrl = 'http://127.0.0.1:{0}' -f $GrafanaPort
$MetricsUrl = 'http://127.0.0.1:{0}/metrics' -f $MetricsPort

function Get-OpsPilotStackPlan {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param()

    return [pscustomobject]@{
        status           = 'WhatIf'
        data_root        = $DataRoot
        metrics_url      = $MetricsUrl
        prometheus_url   = $PrometheusUrl
        grafana_url      = $GrafanaUrl
        metrics_cidrs    = @($script:MetricsScrapeCidrs)
        services         = @($ExporterService, $PrometheusService, $GrafanaService)
        packages         = @(
            foreach ($name in $script:Packages.Keys) {
                $package = $script:Packages[$name]
                [pscustomobject]@{
                    name    = $name
                    version = $package.Version
                    sha256  = $package.Sha256
                    url     = $package.Url
                }
            }
        )
    }
}

function Initialize-OpsPilotDirectory {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
    param([Parameter(Mandatory = $true)][string]$Path)

    if (Test-Path -LiteralPath $Path -PathType Container) {
        return
    }
    if ($PSCmdlet.ShouldProcess($Path, 'Create directory')) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Protect-OpsPilotPath {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Cannot restrict ACLs on a missing path: $Path"
    }
    if (-not $PSCmdlet.ShouldProcess($Path, 'Restrict ACLs to SYSTEM and Administrators')) {
        return
    }
    $inheritance = ''
    if (Test-Path -LiteralPath $Path -PathType Container) {
        $inheritance = '(OI)(CI)'
    }
    & icacls.exe $Path /inheritance:r /grant:r "*S-1-5-18:$($inheritance)F" "*S-1-5-32-544:$($inheritance)F" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to restrict ACLs on $Path."
    }
}

function Grant-OpsPilotPathAccess {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Sid = '*S-1-5-19',
        [string]$Rights = 'M'
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Cannot grant service access to a missing path: $Path"
    }
    if (-not $PSCmdlet.ShouldProcess($Path, "Grant $Sid $Rights")) {
        return
    }
    $grant = '{0}:{1}' -f $Sid, $Rights
    if (Test-Path -LiteralPath $Path -PathType Container) {
        $grant = '{0}:(OI)(CI){1}' -f $Sid, $Rights
    }
    & icacls.exe $Path /grant $grant | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to grant $Sid access to $Path."
    }
}

function Get-OpsPilotVerifiedDownload {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][pscustomobject]$Package
    )

    $fileName = [System.IO.Path]::GetFileName(([uri]$Package.Url).AbsolutePath)
    $target = Join-Path -Path $DownloadRoot -ChildPath $fileName

    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $existing = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
        if ($existing -eq $Package.Sha256) {
            Write-Host "Using cached $Name $($Package.Version) ($($Package.Sha256))."
            return $target
        }
        Write-Warning "Cached $fileName failed SHA256 verification; downloading again."
        if ($PSCmdlet.ShouldProcess($target, 'Remove corrupt cached download')) {
            Remove-Item -LiteralPath $target -Force
        }
    }

    if (-not $PSCmdlet.ShouldProcess($Package.Url, "Download $Name $($Package.Version)")) {
        return $target
    }

    Initialize-OpsPilotDirectory -Path $DownloadRoot -Confirm:$false
    Write-Host "Downloading $Name $($Package.Version) from $($Package.Url)."
    Invoke-WebRequest -Uri $Package.Url -OutFile $target -UseBasicParsing -MaximumRetryCount 3 -RetryIntervalSec 2
    $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
    if ($actual -ne $Package.Sha256) {
        Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        throw "SHA256 mismatch for $Name. Expected $($Package.Sha256) but got $actual. The archive was discarded."
    }
    return $target
}

function Expand-OpsPilotZip {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$Archive,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (-not $PSCmdlet.ShouldProcess($Destination, "Extract $([System.IO.Path]::GetFileName($Archive))")) {
        return $Destination
    }

    $staging = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath ('opspilot-extract-{0}' -f [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Path $staging -Force | Out-Null
        Expand-Archive -LiteralPath $Archive -DestinationPath $staging -Force
        $entries = @(Get-ChildItem -LiteralPath $staging -Force)
        $source = $staging
        if ($entries.Count -eq 1 -and $entries[0].PSIsContainer) {
            $source = $entries[0].FullName
        }
        New-Item -ItemType Directory -Path (Split-Path -Path $Destination -Parent) -Force | Out-Null
        if (Test-Path -LiteralPath $Destination) {
            Remove-Item -LiteralPath $Destination -Recurse -Force
        }
        Move-Item -LiteralPath $source -Destination $Destination
    }
    finally {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    return $Destination
}

function Install-OpsPilotWindowsExporter {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    param(
        [Parameter(Mandatory = $true)][string]$MsiPath
    )

    $enabledCollectors = 'cpu,cs,logical_disk,memory,net,os,service,system,textfile'
    $argumentString = '/i "{0}" /qn /norestart LISTEN_PORT={1} METRICS_PATH=/metrics ENABLED_COLLECTORS={2} REMOVE=FirewallException' -f `
        $MsiPath, $MetricsPort, $enabledCollectors
    if (-not $PSCmdlet.ShouldProcess($ExporterService, 'Install or upgrade windows_exporter MSI')) {
        return
    }

    Write-Host "Installing windows_exporter $($script:Packages.windows_exporter.Version)."
    $process = Start-Process -FilePath 'msiexec.exe' -ArgumentList $argumentString -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "windows_exporter MSI failed with exit code $($process.ExitCode)."
    }
    if ($process.ExitCode -eq 3010) {
        Write-Warning 'windows_exporter MSI reported a required reboot (3010); the service was still verified below.'
    }

    $service = Get-Service -Name $ExporterService -ErrorAction SilentlyContinue
    if ($null -eq $service) {
        throw "windows_exporter service '$ExporterService' was not registered by the MSI."
    }
    if ($service.Status.ToString() -ne 'Running') {
        Start-Service -Name $ExporterService -ErrorAction Stop
    }
}

function Set-OpsPilotMetricsFirewallRule {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    param()

    $name = 'OpsPilot-Metrics-{0}' -f $MetricsPort
    $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $PSCmdlet.ShouldProcess($name, 'Create or update the metrics firewall rule')) {
        return
    }

    if ($null -eq $existing) {
        New-NetFirewallRule -DisplayName $name -Name $name -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $MetricsPort -RemoteAddress $script:MetricsScrapeCidrs -Profile Any `
            -Enabled True -ErrorAction Stop | Out-Null
    }
    else {
        Set-NetFirewallRule -DisplayName $name -Enabled True -Action Allow -Profile Any -ErrorAction Stop
        Set-NetFirewallPortFilter -Protocol TCP -LocalPort $MetricsPort -AssociatedNetFirewallRule $existing -ErrorAction Stop
        Set-NetFirewallAddressFilter -RemoteAddress $script:MetricsScrapeCidrs -AssociatedNetFirewallRule $existing -ErrorAction Stop
    }
}

function Write-OpsPilotPrometheusConfig {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
    param()

    if (-not $PSCmdlet.ShouldProcess($PrometheusConfig, 'Write prometheus.yml')) {
        return
    }
    $config = @'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: windows_exporter
    static_configs:
      - targets:
          - 127.0.0.1:9182

  - job_name: prometheus
    static_configs:
      - targets:
          - 127.0.0.1:9090
'@
    Set-Content -LiteralPath $PrometheusConfig -Value $config -Encoding utf8
}

function Invoke-OpsPilotNssm {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & $NssmExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "nssm $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Stop-OpsPilotServiceIfRunning {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    param([Parameter(Mandatory = $true)][string]$Name)

    $existing = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $existing -or $existing.Status.ToString() -eq 'Stopped') {
        return
    }
    if ($PSCmdlet.ShouldProcess($Name, 'Stop service before replacing binaries')) {
        Stop-Service -Name $Name -Force -ErrorAction Stop
    }
}

function Set-OpsPilotNssmService {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Application,
        [Parameter(Mandatory = $true)][string]$AppParameters,
        [Parameter(Mandatory = $true)][string]$AppDirectory,
        [Parameter(Mandatory = $true)][string]$DisplayName,
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath,
        [string]$ServiceAccount = 'NT AUTHORITY\LocalService'
    )

    if (-not $PSCmdlet.ShouldProcess($Name, 'Register or update the NSSM-managed service')) {
        return
    }

    $existing = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if ($existing.Status.ToString() -ne 'Stopped') {
            Stop-Service -Name $Name -Force -ErrorAction Stop
        }
    }
    else {
        Invoke-OpsPilotNssm -Arguments @('install', $Name, $Application)
    }

    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'Application', $Application)
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'AppParameters', $AppParameters)
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'AppDirectory', $AppDirectory)
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'DisplayName', $DisplayName)
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'Description', $Description)
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'Start', 'SERVICE_AUTO_START')
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'ObjectName', $ServiceAccount)
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'AppStdout', $StdoutPath)
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'AppStderr', $StderrPath)
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'AppRotateFiles', '1')
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'AppRotateOnline', '1')
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'AppRotateBytes', '10485760')
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'AppExit', 'Default', 'Restart')
    Invoke-OpsPilotNssm -Arguments @('set', $Name, 'AppRestartDelay', '5000')

    Start-Service -Name $Name -ErrorAction Stop
}

function Wait-OpsPilotHttpEndpoint {
    [CmdletBinding()]
    [OutputType([object])]
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 90,
        [scriptblock]$Validator
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = 'no attempt was made'
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
            if ($null -eq $Validator) {
                return $response
            }
            $valid = & $Validator $response
            if ($valid) {
                return $response
            }
            $lastError = "response from $Url did not satisfy its health predicate"
        }
        catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    throw "Timed out after $TimeoutSeconds seconds waiting for $Url. Last error: $lastError"
}

function Wait-OpsPilotPrometheusTarget {
    [CmdletBinding()]
    param([int]$TimeoutSeconds = 90)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = 'no scrape target was reported'
    while ((Get-Date) -lt $deadline) {
        try {
            $document = Invoke-RestMethod -Uri ('{0}/api/v1/targets?state=active' -f $PrometheusUrl) -TimeoutSec 10 -ErrorAction Stop
            $target = @($document.data.activeTargets) |
                Where-Object { $_.labels.job -eq 'windows_exporter' } |
                Select-Object -First 1
            if ($null -ne $target -and $target.health -eq 'up') {
                return $target
            }
            if ($null -ne $target) {
                $lastError = "windows_exporter target health is '$($target.health)': $($target.lastError)"
            }
            else {
                $lastError = 'windows_exporter target is not present in Prometheus'
            }
        }
        catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    throw "Prometheus did not scrape windows_exporter within $TimeoutSeconds seconds. Last error: $lastError"
}

function ConvertTo-OpsPilotSecureString {
    [CmdletBinding()]
    [OutputType([securestring])]
    param([Parameter(Mandatory = $true)][string]$Value)

    $secure = [System.Security.SecureString]::new()
    foreach ($character in $Value.ToCharArray()) {
        $secure.AppendChar($character)
    }
    $secure.MakeReadOnly()
    return $secure
}

function Get-OpsPilotGrafanaAdminPassword {
    [CmdletBinding()]
    [OutputType([securestring])]
    param()

    if ($null -ne $GrafanaAdminPassword) {
        return $GrafanaAdminPassword
    }
    if ((Test-Path -LiteralPath $GrafanaConfig -PathType Leaf) -and -not $Force.IsPresent) {
        $match = [regex]::Match(
            (Get-Content -LiteralPath $GrafanaConfig -Raw),
            '(?m)^admin_password\s*=\s*(.+)$'
        )
        if ($match.Success -and -not [string]::IsNullOrWhiteSpace($match.Groups[1].Value)) {
            return ConvertTo-OpsPilotSecureString -Value $match.Groups[1].Value.Trim()
        }
    }

    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $password = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    return ConvertTo-OpsPilotSecureString -Value $password
}

function Write-OpsPilotGrafanaConfig {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][securestring]$AdminPassword)

    if (-not $PSCmdlet.ShouldProcess($GrafanaConfig, 'Write grafana.ini and admin password file')) {
        return $GrafanaConfig
    }
    $plainPassword = [System.Net.NetworkCredential]::new('', $AdminPassword).Password

    $config = @"
[paths]
data = $GrafanaData
logs = $GrafanaLogs
provisioning = $GrafanaProvisioning

[server]
protocol = http
http_addr = 127.0.0.1
http_port = $GrafanaPort
domain = localhost
enforce_domain = false
root_url = $GrafanaUrl

[security]
admin_user = admin
admin_password = $plainPassword
disable_gravatar = true
cookie_secure = false
cookie_samesite = strict

[analytics]
reporting_enabled = false
check_for_updates = false
check_for_plugin_updates = false

[log]
mode = console file
level = info

[plugins]
allow_loading_unsigned_plugins =
"@

    Set-Content -LiteralPath $GrafanaConfig -Value $config -Encoding utf8
    Set-Content -LiteralPath $GrafanaPasswordFile -Value $plainPassword -Encoding utf8 -NoNewline
    Protect-OpsPilotPath -Path $GrafanaConfig -Confirm:$false
    Protect-OpsPilotPath -Path $GrafanaPasswordFile -Confirm:$false
    Grant-OpsPilotPathAccess -Path $GrafanaConfig -Rights 'R' -Confirm:$false
    return $GrafanaConfig
}

function Copy-OpsPilotGrafanaProvisioning {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
    param()

    $source = Join-Path -Path $PSScriptRoot -ChildPath 'grafana'
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Grafana provisioning source not found: $source"
    }
    if (-not $PSCmdlet.ShouldProcess($GrafanaProvisioning, 'Copy Grafana provisioning and dashboards')) {
        return
    }

    $datasourceTarget = Join-Path -Path $GrafanaProvisioning -ChildPath 'datasources'
    $dashboardProviderTarget = Join-Path -Path $GrafanaProvisioning -ChildPath 'dashboards'
    Initialize-OpsPilotDirectory -Path $datasourceTarget -Confirm:$false
    Initialize-OpsPilotDirectory -Path $dashboardProviderTarget -Confirm:$false
    Initialize-OpsPilotDirectory -Path $GrafanaDashboards -Confirm:$false

    Copy-Item -LiteralPath (Join-Path $source 'provisioning\datasources\prometheus.yaml') `
        -Destination (Join-Path $datasourceTarget 'prometheus.yaml') -Force
    Copy-Item -LiteralPath (Join-Path $source 'dashboards\opspilot.json') `
        -Destination (Join-Path $GrafanaDashboards 'opspilot.json') -Force

    $provider = Get-Content -LiteralPath (Join-Path $source 'provisioning\dashboards\opspilot.yaml') -Raw
    $provider = $provider.Replace('C:\ProgramData\OpsPilot\grafana\dashboards', $GrafanaDashboards)
    Set-Content -LiteralPath (Join-Path $dashboardProviderTarget 'opspilot.yaml') -Value $provider -Encoding utf8
}

function Write-OpsPilotStackManifest {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$GrafanaPasswordInfo
    )

    if (-not $PSCmdlet.ShouldProcess($StackManifest, 'Write stack manifest')) {
        return
    }
    $manifest = [ordered]@{
        version          = '1'
        installed_at     = (Get-Date).ToUniversalTime().ToString('o')
        host             = $env:COMPUTERNAME
        packages         = [ordered]@{}
        services         = [ordered]@{
            windows_exporter = $ExporterService
            prometheus       = $PrometheusService
            grafana          = $GrafanaService
        }
        endpoints        = [ordered]@{
            windows_exporter = $MetricsUrl
            prometheus       = $PrometheusUrl
            grafana          = $GrafanaUrl
        }
        metrics_cidrs    = @($script:MetricsScrapeCidrs)
        grafana_admin    = [ordered]@{
            user                = 'admin'
            password_file       = $GrafanaPasswordFile
            password_generated  = [bool]$GrafanaPasswordInfo.generated
        }
    }
    foreach ($name in $script:Packages.Keys) {
        $package = $script:Packages[$name]
        $manifest.packages[$name] = [ordered]@{
            version = $package.Version
            sha256  = $package.Sha256
            url     = $package.Url
        }
    }
    Set-Content -LiteralPath $StackManifest -Value ($manifest | ConvertTo-Json -Depth 8) -Encoding utf8
}

if ($WhatIfPreference) {
    $plan = Get-OpsPilotStackPlan
    Write-Host 'OpsPilot observability stack plan (no changes made):'
    foreach ($package in $plan.packages) {
        Write-Host ("  {0,-16} {1,-8} sha256={2}" -f $package.name, $package.version, $package.sha256)
    }
    Write-Host ("  services         {0}" -f ($plan.services -join ', '))
    Write-Host ("  metrics endpoint {0} (firewalled to {1})" -f $plan.metrics_url, ($plan.metrics_cidrs -join ', '))
    Write-Host ("  prometheus       {0}" -f $plan.prometheus_url)
    Write-Host ("  grafana          {0}" -f $plan.grafana_url)
    Write-Host ("  data root        {0}" -f $plan.data_root)
    return $plan
}

if (-not (Test-Path -LiteralPath $DataRoot -PathType Container)) {
    if (-not $PSCmdlet.ShouldProcess($DataRoot, 'Create the OpsPilot data root')) {
        return (Get-OpsPilotStackPlan)
    }
    New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
}

Initialize-OpsPilotDirectory -Path $DownloadRoot -Confirm:$false
Initialize-OpsPilotDirectory -Path $LogRoot -Confirm:$false
Initialize-OpsPilotDirectory -Path $PrometheusRoot -Confirm:$false
Initialize-OpsPilotDirectory -Path $PrometheusData -Confirm:$false
Initialize-OpsPilotDirectory -Path $GrafanaRoot -Confirm:$false
Initialize-OpsPilotDirectory -Path $GrafanaData -Confirm:$false
Initialize-OpsPilotDirectory -Path $GrafanaLogs -Confirm:$false
Initialize-OpsPilotDirectory -Path $GrafanaProvisioning -Confirm:$false
Initialize-OpsPilotDirectory -Path $GrafanaDashboards -Confirm:$false
Initialize-OpsPilotDirectory -Path $StackRoot -Confirm:$false

$archives = [ordered]@{}
foreach ($name in $script:Packages.Keys) {
    $archives[$name] = Get-OpsPilotVerifiedDownload -Name $name -Package $script:Packages[$name] -Confirm:$false
}

$grafanaPasswordGenerated = $false
$grafanaPasswordValue = Get-OpsPilotGrafanaAdminPassword
if ($null -eq $GrafanaAdminPassword -and -not (Test-Path -LiteralPath $GrafanaPasswordFile -PathType Leaf)) {
    $grafanaPasswordGenerated = $true
}

Install-OpsPilotWindowsExporter -MsiPath $archives['windows_exporter'] -Confirm:$false
Set-OpsPilotMetricsFirewallRule -Confirm:$false
Stop-OpsPilotServiceIfRunning -Name $PrometheusService -Confirm:$false
Stop-OpsPilotServiceIfRunning -Name $GrafanaService -Confirm:$false

Expand-OpsPilotZip -Archive $archives['nssm'] -Destination $NssmRootVersion -Confirm:$false | Out-Null
if (-not (Test-Path -LiteralPath $NssmExe -PathType Leaf)) {
    throw "NSSM executable not found after extraction: $NssmExe"
}
if (-not (Test-Path -LiteralPath $PrometheusExe -PathType Leaf) -or $Force.IsPresent) {
    Expand-OpsPilotZip -Archive $archives['prometheus'] -Destination $PrometheusRootVersion -Confirm:$false | Out-Null
}
if (-not (Test-Path -LiteralPath $PrometheusExe -PathType Leaf)) {
    throw "Prometheus executable not found after extraction: $PrometheusExe"
}
if (-not (Test-Path -LiteralPath $GrafanaExe -PathType Leaf) -or $Force.IsPresent) {
    Expand-OpsPilotZip -Archive $archives['grafana'] -Destination $GrafanaRootVersion -Confirm:$false | Out-Null
}
if (-not (Test-Path -LiteralPath $GrafanaExe -PathType Leaf)) {
    throw "Grafana executable not found after extraction: $GrafanaExe"
}

Write-OpsPilotPrometheusConfig -Confirm:$false
Copy-OpsPilotGrafanaProvisioning -Confirm:$false
Write-OpsPilotGrafanaConfig -AdminPassword $grafanaPasswordValue -Confirm:$false | Out-Null

Grant-OpsPilotPathAccess -Path $PrometheusRoot -Confirm:$false
Grant-OpsPilotPathAccess -Path $GrafanaData -Confirm:$false
Grant-OpsPilotPathAccess -Path $GrafanaLogs -Confirm:$false
Grant-OpsPilotPathAccess -Path $GrafanaProvisioning -Confirm:$false
Grant-OpsPilotPathAccess -Path $GrafanaDashboards -Confirm:$false
Grant-OpsPilotPathAccess -Path $LogRoot -Confirm:$false

$prometheusArguments = '--config.file="{0}" --storage.tsdb.path="{1}" --storage.tsdb.retention.time=30d --web.listen-address=127.0.0.1:{2}' -f `
    $PrometheusConfig, $PrometheusData, $PrometheusPort
Set-OpsPilotNssmService -Name $PrometheusService -Application $PrometheusExe `
    -AppParameters $prometheusArguments -AppDirectory $PrometheusRootVersion `
    -DisplayName 'OpsPilot Prometheus' -Description 'OpsPilot metrics store (Prometheus, local-only).' `
    -StdoutPath (Join-Path $LogRoot 'prometheus.out.log') `
    -StderrPath (Join-Path $LogRoot 'prometheus.err.log') -Confirm:$false

$grafanaArguments = 'server --homepath "{0}" --config "{1}"' -f $GrafanaRootVersion, $GrafanaConfig
Set-OpsPilotNssmService -Name $GrafanaService -Application $GrafanaExe `
    -AppParameters $grafanaArguments -AppDirectory $GrafanaRootVersion `
    -DisplayName 'OpsPilot Grafana' -Description 'OpsPilot dashboards (Grafana, local-only).' `
    -StdoutPath (Join-Path $LogRoot 'grafana.out.log') `
    -StderrPath (Join-Path $LogRoot 'grafana.err.log') -Confirm:$false

Write-Host 'Waiting for the observability endpoints to become healthy.'
Wait-OpsPilotHttpEndpoint -Url $MetricsUrl -Validator {
    param($response)
    $response.Content -match 'windows_cpu_time_total' -and
    $response.Content -match 'windows_os_physical_memory_free_bytes' -and
    $response.Content -match 'windows_logical_disk_free_bytes'
} | Out-Null
Wait-OpsPilotHttpEndpoint -Url ('{0}/-/ready' -f $PrometheusUrl) | Out-Null
$scrapeTarget = Wait-OpsPilotPrometheusTarget
Wait-OpsPilotHttpEndpoint -Url ('{0}/api/health' -f $GrafanaUrl) -Validator {
    param($response)
    $health = $response.Content | ConvertFrom-Json
    $health.database -eq 'ok'
} | Out-Null

Write-OpsPilotStackManifest -GrafanaPasswordInfo ([pscustomobject]@{
    generated = $grafanaPasswordGenerated
}) -Confirm:$false

$result = [pscustomobject]@{
    status          = 'Installed'
    data_root       = $DataRoot
    windows_exporter = [pscustomobject]@{
        version = $script:Packages.windows_exporter.Version
        service = $ExporterService
        url     = $MetricsUrl
    }
    prometheus      = [pscustomobject]@{
        version = $script:Packages.prometheus.Version
        service = $PrometheusService
        url     = $PrometheusUrl
        target  = $scrapeTarget.scrapeUrl
    }
    grafana         = [pscustomobject]@{
        version          = $script:Packages.grafana.Version
        service          = $GrafanaService
        url              = $GrafanaUrl
        admin_user       = 'admin'
        password_file    = $GrafanaPasswordFile
        password_generated = $grafanaPasswordGenerated
    }
    manifest       = $StackManifest
}

if ($grafanaPasswordGenerated) {
    Write-Host "A new Grafana admin password was generated and stored at $GrafanaPasswordFile (ACL: SYSTEM/Administrators)."
}
Write-Host 'Observability stack installed and verified.'
return $result
