<#
.SYNOPSIS
    Windows-side entry point for OpsPilot Cloud installation, upgrade, and deploy.

.DESCRIPTION
    Implements IMPLEMENTATION.md §3.2, §7, and §9. Reads the phase's Handoff
    Manifest, installs the control plane as a native Windows Service, runs the
    Windows integration suite, and records the result in the manifest.

    The upgrade path is the literal §9 sequence:
      a. checkpoint  b. stop service  c. copy payload  d. start service
      e. health-check  f. automatic rollback on failure  g. write manifest result

    No step invokes bash, WSL, or a Linux container runtime.

.PARAMETER Phase
    Phase id matching handoff/HANDOFF_<Phase>.md.

.PARAMETER Upgrade
    Run the §9 upgrade sequence against an existing installation.

.PARAMETER Deploy
    After install, run a blue/green application deployment. Requires -Domain,
    -Email, and -CutoverApprovalRef.

.PARAMETER WhatIf
    Preview the plan without stopping services, copying files, or writing state.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]+$')]
    [string]$Phase,

    [string]$InstallRoot = 'C:\OpsPilotCloud',
    [string]$DataDir = 'C:\ProgramData\OpsPilot',
    [string]$ServiceAccount = 'NT AUTHORITY\LocalService',
    [System.Management.Automation.PSCredential]$ServiceCredential,
    [switch]$Upgrade,
    [switch]$Force,
    [switch]$SkipIntegrationTests,

    [switch]$Deploy,
    [string]$Domain,
    [string]$Email,
    [string]$CutoverApprovalRef,
    [switch]$AcmeStaging,

    [int]$HealthTimeoutSeconds = 120,
    [int]$HealthMaxRetries = 2,

    [string]$DashboardHost = '127.0.0.1',
    [ValidateRange(1, 65535)][int]$DashboardPort = 8090
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $IsWindows) {
    throw 'install.ps1 is Windows-only. The Mac-side authoring host must use make lint/test/bundle instead.'
}

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path -Path $PSScriptRoot -ChildPath '..\..')).Path
$modulePath = Join-Path -Path $RepoRoot -ChildPath 'powershell\modules'
Import-Module (Join-Path -Path $modulePath -ChildPath 'OpsPilot.Health.psm1') -Force
Import-Module (Join-Path -Path $modulePath -ChildPath 'OpsPilot.Install.psm1') -Force

if (-not $WhatIfPreference -and -not (Test-OpsPilotAdministrator)) {
    throw 'install.ps1 must run from an elevated PowerShell session.'
}

$manifestPath = Join-Path -Path $RepoRoot -ChildPath "handoff\HANDOFF_$Phase.md"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Handoff Manifest not found: $manifestPath. Run make bundle PHASE=$Phase on the Mac side first."
}
$manifestTasks = @(
    Get-Content -LiteralPath $manifestPath |
        Where-Object { $_ -match '^- IMPL-[0-9]+' }
)
Write-Host "OpsPilot install plan"
Write-Host "  phase:       $Phase"
Write-Host "  mode:        $(if ($Upgrade) { 'upgrade' } else { 'install' })"
Write-Host "  repo:        $RepoRoot"
Write-Host "  install root: $InstallRoot"
Write-Host "  data dir:    $DataDir"
Write-Host "  service:     OpsPilotControlPlane ($ServiceAccount)"
Write-Host ('  dashboard:   OpsPilotDashboardUI ({0}:{1})' -f $DashboardHost, $DashboardPort)
Write-Host "  manifest:    $manifestPath ($($manifestTasks.Count) task line(s))"

$prerequisites = Test-OpsPilotPrerequisite -Phase $Phase -InstallRoot $RepoRoot
if (-not $prerequisites.ok) {
    throw "Missing prerequisites: $($prerequisites.missing -join ', '). See IMPLEMENTATION.md §3.3."
}

if ($Upgrade) {
    $existing = Get-Service -Name 'OpsPilotControlPlane' -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        throw 'Upgrade requires an existing OpsPilotControlPlane service. Run a normal install first.'
    }
    if (-not $PSCmdlet.ShouldProcess('OpsPilotControlPlane', "Upgrade to phase $Phase")) {
        Write-Host 'WhatIf: would checkpoint, stop, copy payload, start, health-check, and roll back on failure.'
        return
    }

    # a. COMP-05 snapshot, blocking.
    $checkpointId = Invoke-OpsPilotCheckpoint -InstallRoot $RepoRoot -Tag "pre-upgrade-$Phase"
    Write-Host "Checkpoint: $checkpointId"

    # b. stop service
    Stop-Service -Name 'OpsPilotControlPlane' -Force -ErrorAction Stop
    Write-Host 'Stopped OpsPilotControlPlane.'
    $existingDashboard = Get-Service -Name 'OpsPilotDashboardUI' -ErrorAction SilentlyContinue
    if ($null -ne $existingDashboard -and $existingDashboard.Status.ToString() -ne 'Stopped') {
        Stop-Service -Name 'OpsPilotDashboardUI' -Force -ErrorAction Stop
        Write-Host 'Stopped OpsPilotDashboardUI.'
    }

    # c. copy new binaries/config into place, then refresh the venv
    Copy-OpsPilotPayload -Source $RepoRoot -Destination $InstallRoot | Out-Null
    Initialize-OpsPilotPythonEnvironment -InstallRoot $InstallRoot -Force | Out-Null
    Write-Host "Copied payload to $InstallRoot."

    # d. start service
    Start-Service -Name 'OpsPilotControlPlane' -ErrorAction Stop
    Write-Host 'Started OpsPilotControlPlane.'

    # e. health-check with the same timeout/retry policy used by tool contracts
    $health = Invoke-OpsPilotHealthCheck -ServiceName 'OpsPilotControlPlane' `
        -TimeoutSeconds $HealthTimeoutSeconds -MaxRetries $HealthMaxRetries -RetryDelaySeconds 5
    if (-not $health.healthy) {
        # f. automatic rollback, then exit non-zero.
        Write-Warning "Post-upgrade health check failed: $($health.error). Rolling back $checkpointId."
        $rollback = Invoke-OpsPilotRollback -InstallRoot $InstallRoot -CheckpointId $checkpointId
        Write-OpsPilotHandoffResult -ManifestPath $manifestPath -Phase $Phase -Status 'Upgrade failed; rolled back' -Details @(
            "checkpoint: $checkpointId",
            "health: $($health.error)",
            "rollback restored: $($rollback.restored)",
            "rollback verified: $($rollback.verified)"
        ) | Out-Null
        throw "Upgrade failed and rollback was attempted for checkpoint $checkpointId."
    }

    # g. success recorded in the manifest
    try {
        $dashboard = Install-OpsPilotDashboard -InstallRoot $InstallRoot -DataDir $DataDir `
            -ServiceAccount $ServiceAccount -ServiceCredential $ServiceCredential `
            -HostName $DashboardHost -Port $DashboardPort `
            -HealthTimeoutSeconds ([Math]::Min(300, [Math]::Max(1, $HealthTimeoutSeconds))) `
            -HealthMaxRetries $HealthMaxRetries
    }
    catch {
        Write-OpsPilotHandoffResult -ManifestPath $manifestPath -Phase $Phase -Status 'Control plane upgraded; dashboard FAILED' -Details @(
            "checkpoint: $checkpointId",
            "health attempts: $($health.attempts)",
            "dashboard error: $($_.Exception.Message)"
        ) | Out-Null
        throw "Control-plane upgrade succeeded but dashboard install failed: $($_.Exception.Message)"
    }
    Write-OpsPilotHandoffResult -ManifestPath $manifestPath -Phase $Phase -Status 'Upgrade succeeded' -Details @(
        "checkpoint: $checkpointId",
        "health attempts: $($health.attempts)",
        "dashboard: $($dashboard.status) at $($dashboard.url)"
    ) | Out-Null
    Write-Host 'Upgrade completed and verified.'
    return
}

if (-not $PSCmdlet.ShouldProcess($InstallRoot, "Install OpsPilot phase $Phase")) {
    Write-Host 'WhatIf: would copy payload, create venv, register/start the control-plane and dashboard services, run integration tests, and record the result.'
    return
}

Copy-OpsPilotPayload -Source $RepoRoot -Destination $InstallRoot | Out-Null
Initialize-OpsPilotPythonEnvironment -InstallRoot $InstallRoot -Force:$Force | Out-Null
$service = Install-OpsPilotControlPlane -InstallRoot $InstallRoot -DataDir $DataDir `
    -ServiceAccount $ServiceAccount -ServiceCredential $ServiceCredential `
    -HealthTimeoutSeconds ([Math]::Min(300, [Math]::Max(1, $HealthTimeoutSeconds))) `
    -HealthMaxRetries $HealthMaxRetries
Write-Host "Service $($service.service_name) is $($service.status)."
try {
    $dashboard = Install-OpsPilotDashboard -InstallRoot $InstallRoot -DataDir $DataDir `
        -ServiceAccount $ServiceAccount -ServiceCredential $ServiceCredential `
        -HostName $DashboardHost -Port $DashboardPort `
        -HealthTimeoutSeconds ([Math]::Min(300, [Math]::Max(1, $HealthTimeoutSeconds))) `
        -HealthMaxRetries $HealthMaxRetries
}
catch {
    Write-OpsPilotHandoffResult -ManifestPath $manifestPath -Phase $Phase -Status 'Install completed; dashboard FAILED' -Details @(
        "control plane: $($service.status)",
        "dashboard error: $($_.Exception.Message)"
    ) | Out-Null
    throw "Control plane installed but dashboard install failed: $($_.Exception.Message)"
}
Write-Host "Dashboard $($dashboard.service_name) is $($dashboard.status) at $($dashboard.url)."

if (-not $SkipIntegrationTests) {
    $venvPython = Get-OpsPilotPythonPath -InstallRoot $InstallRoot
    Push-Location $InstallRoot
    try {
        & $venvPython -m pytest tests/integration -q
        $testExit = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($testExit -ne 0) {
        Write-OpsPilotHandoffResult -ManifestPath $manifestPath -Phase $Phase -Status 'Install completed; integration tests FAILED' -Details @(
            "pytest exit code: $testExit"
        ) | Out-Null
        throw "Windows integration tests failed with exit code $testExit."
    }
}

if ($Deploy) {
    if ([string]::IsNullOrWhiteSpace($Domain) -or [string]::IsNullOrWhiteSpace($Email) -or [string]::IsNullOrWhiteSpace($CutoverApprovalRef)) {
        throw '-Deploy requires -Domain, -Email, and -CutoverApprovalRef (the one-time cutover approval).'
    }
    $arguments = @(
        '-m', 'orchestrator.cli', 'deploy',
        '--deployment-config', (Join-Path -Path $InstallRoot -ChildPath 'config\defaults\deployment.yaml'),
        '--domain', $Domain,
        '--email', $Email,
        '--cutover-approval-ref', $CutoverApprovalRef
    )
    if ($AcmeStaging) { $arguments += '--acme-staging' }
    $deployOutput = Invoke-OpsPilotPythonModule -InstallRoot $InstallRoot -Arguments $arguments
    Write-Host $deployOutput
}

Write-OpsPilotHandoffResult -ManifestPath $manifestPath -Phase $Phase -Status 'Install succeeded' -Details @(
    "service: $($service.service_name) ($($service.status))",
    "binary: $($service.binary_path)",
    "dashboard: $($dashboard.service_name) ($($dashboard.status)) at $($dashboard.url)",
    "integration tests: $(if ($SkipIntegrationTests) { 'skipped by operator' } else { 'passed' })"
) | Out-Null
Write-Host 'Install completed and verified.'
