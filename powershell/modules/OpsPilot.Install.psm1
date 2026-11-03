# OpsPilot.Install — Windows-native install, upgrade, checkpoint, and rollback.
# All mutating functions support -WhatIf; no step requires bash or WSL.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ControlPlaneServiceName = 'OpsPilotControlPlane'
$script:DashboardServiceName = 'OpsPilotDashboardUI'

function Test-OpsPilotAdministrator {
    [CmdletBinding()]
    [OutputType([bool])]
    param()

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-OpsPilotPythonPath {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [string]$InstallRoot = (Split-Path -Path (Split-Path -Path $PSScriptRoot -Parent) -Parent)
    )

    $venv = Join-Path -Path $InstallRoot -ChildPath '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv -PathType Leaf) {
        return $venv
    }
    $command = Get-Command -Name 'python.exe' -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        $command = Get-Command -Name 'python' -ErrorAction SilentlyContinue
    }
    if ($null -eq $command) {
        throw 'Python 3.11+ was not found. Install it with: winget install --id Python.Python.3.11'
    }
    return $command.Source
}

function Test-OpsPilotPrerequisite {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [string]$Phase = '0',
        [string]$InstallRoot = (Split-Path -Path (Split-Path -Path $PSScriptRoot -Parent) -Parent)
    )

    $missing = New-Object System.Collections.Generic.List[string]
    $details = [ordered]@{}

    if ($PSVersionTable.PSVersion.Major -lt 7) {
        $missing.Add('PowerShell 7+')
    }
    $details['powershell'] = $PSVersionTable.PSVersion.ToString()

    try {
        $python = Get-OpsPilotPythonPath -InstallRoot $InstallRoot
        $pythonVersion = (& $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
        $details['python'] = "$python ($pythonVersion)"
        $parts = $pythonVersion.Split('.')
        if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
            $missing.Add('Python 3.11+')
        }
    }
    catch {
        $missing.Add('Python 3.11+')
        $details['python'] = $_.Exception.Message
    }

    $phaseNumber = 0
    if (-not [int]::TryParse($Phase, [ref]$phaseNumber)) {
        $phaseNumber = 0
    }

    $requiredCommands = @()
    if ($phaseNumber -ge 1) { $requiredCommands += 'terraform'; $requiredCommands += 'caddy' }
    if ($phaseNumber -ge 2) { $requiredCommands += 'docker' }
    foreach ($name in $requiredCommands) {
        $command = Get-Command -Name $name -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            $missing.Add($name)
            $details[$name] = 'not found on PATH'
        }
        else {
            $details[$name] = $command.Source
        }
    }

    return [pscustomobject]@{
        ok      = ($missing.Count -eq 0)
        missing = @($missing)
        details = [pscustomobject]$details
    }
}

function Initialize-OpsPilotPythonEnvironment {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [switch]$Force
    )

    $venvPath = Join-Path -Path $InstallRoot -ChildPath '.venv'
    $venvPython = Join-Path -Path $venvPath -ChildPath 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf) -or $Force.IsPresent) {
        if (-not $PSCmdlet.ShouldProcess($venvPath, 'Create Python virtual environment')) {
            return [pscustomobject]@{ path = $venvPath; python = $venvPython; status = 'WhatIf' }
        }
        $basePython = Get-OpsPilotPythonPath -InstallRoot $InstallRoot
        & $basePython -m venv $venvPath
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create virtual environment at $venvPath."
        }
    }

    $requirements = @(
        (Join-Path -Path $InstallRoot -ChildPath 'requirements.txt'),
        (Join-Path -Path $InstallRoot -ChildPath 'requirements-dev.txt')
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }

    if (-not $PSCmdlet.ShouldProcess($venvPython, 'Install pinned Python dependencies')) {
        return [pscustomobject]@{ path = $venvPath; python = $venvPython; status = 'WhatIf' }
    }
    & $venvPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Failed to upgrade pip.' }
    foreach ($file in $requirements) {
        & $venvPython -m pip install --disable-pip-version-check -r $file
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install Python dependencies from $file."
        }
    }

    return [pscustomobject]@{ path = $venvPath; python = $venvPython; status = 'Ready' }
}

function Copy-OpsPilotPayload {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $sourceFull = (Resolve-Path -LiteralPath $Source).Path.TrimEnd('\')
    if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
        if (-not $PSCmdlet.ShouldProcess($Destination, 'Create install root')) {
            return [pscustomobject]@{ source = $sourceFull; destination = $Destination; status = 'WhatIf' }
        }
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    }
    $destinationFull = (Resolve-Path -LiteralPath $Destination).Path.TrimEnd('\')
    if ($sourceFull -eq $destinationFull) {
        return [pscustomobject]@{ source = $sourceFull; destination = $destinationFull; status = 'Unchanged' }
    }
    if ($destinationFull.StartsWith($sourceFull + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to copy the repository into one of its own subdirectories.'
    }
    if (-not $PSCmdlet.ShouldProcess($destinationFull, "Copy payload from $sourceFull")) {
        return [pscustomobject]@{ source = $sourceFull; destination = $destinationFull; status = 'WhatIf' }
    }

    $excluded = @('.git', '.venv', '.tools', '.tmp', '.pip-cache', 'dist', 'build', '__pycache__')
    foreach ($item in Get-ChildItem -LiteralPath $sourceFull -Force) {
        if ($excluded -contains $item.Name) { continue }
        Copy-Item -LiteralPath $item.FullName -Destination $destinationFull -Recurse -Force
    }
    return [pscustomobject]@{ source = $sourceFull; destination = $destinationFull; status = 'Copied' }
}

function Grant-OpsPilotDirectoryAccess {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Sid = '*S-1-5-19'
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Cannot grant service access to a missing directory: $Path"
    }
    if (-not $PSCmdlet.ShouldProcess($Path, "Grant $Sid modify access")) {
        return
    }
    & icacls.exe $Path /grant ('{0}:(OI)(CI)M' -f $Sid) | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to grant $Sid access to $Path."
    }
}

function Install-OpsPilotControlPlane {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [string]$DataDir = 'C:\ProgramData\OpsPilot',
        [string]$ServiceAccount = 'NT AUTHORITY\LocalService',
        [System.Management.Automation.PSCredential]$ServiceCredential,
        [int]$HealthTimeoutSeconds = 30,
        [int]$HealthMaxRetries = 5
    )

    $serviceName = $script:ControlPlaneServiceName
    $python = Get-OpsPilotPythonPath -InstallRoot $InstallRoot
    $entryPoint = Join-Path -Path $InstallRoot -ChildPath 'run_control_plane.py'
    if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
        throw "Control-plane entry point not found: $entryPoint"
    }
    if (-not (Test-Path -LiteralPath $DataDir -PathType Container)) {
        if ($PSCmdlet.ShouldProcess($DataDir, 'Create control-plane data directory')) {
            New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
        }
    }

    $configDir = Join-Path -Path $InstallRoot -ChildPath 'config\defaults'
    $binaryPath = '"{0}" "{1}" --data-dir "{2}" --config-dir "{3}"' -f $python, $entryPoint, $DataDir, $configDir
    $existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    $accountName = $ServiceAccount
    if ($null -ne $ServiceCredential) {
        $accountName = $ServiceCredential.UserName
    }

    if (-not $PSCmdlet.ShouldProcess($serviceName, 'Register and start the OpsPilot control-plane service')) {
        return [pscustomobject]@{
            service_name = $serviceName
            status       = 'WhatIf'
            binary_path  = $binaryPath
            account      = $accountName
        }
    }

    $builtInSid = switch ($accountName) {
        'NT AUTHORITY\LocalService' { '*S-1-5-19' }
        'LocalService' { '*S-1-5-19' }
        'NT AUTHORITY\NetworkService' { '*S-1-5-20' }
        'NetworkService' { '*S-1-5-20' }
        default { $null }
    }
    if ($null -ne $builtInSid) {
        Grant-OpsPilotDirectoryAccess -Path $DataDir -Sid $builtInSid -Confirm:$false
    }

    if ($null -ne $existing) {
        if ($existing.Status.ToString() -ne 'Stopped') {
            Stop-Service -Name $serviceName -Force -ErrorAction Stop
        }
        Set-Service -Name $serviceName -BinaryPathName $binaryPath -StartupType Automatic -ErrorAction Stop
        if ($null -ne $ServiceCredential) {
            Set-Service -Name $serviceName -Credential $ServiceCredential -ErrorAction Stop
        }
        else {
            & sc.exe config $serviceName 'obj=' $accountName | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "sc.exe config failed to set the account for '$serviceName'."
            }
        }
    }
    else {
        $parameters = @{
            Name           = $serviceName
            BinaryPathName = $binaryPath
            DisplayName    = 'OpsPilot Control Plane'
            Description    = 'OpsPilot Cloud health, remediation, and reporting control plane.'
            StartupType    = 'Automatic'
        }
        if ($null -ne $ServiceCredential) {
            $parameters['Credential'] = $ServiceCredential
        }
        New-Service @parameters | Out-Null
        if ($null -eq $ServiceCredential) {
            & sc.exe config $serviceName 'obj=' $accountName | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "sc.exe config failed to set the account for new service '$serviceName'."
            }
        }
    }

    Start-Service -Name $serviceName -ErrorAction Stop
    $installed = Get-CimInstance -ClassName Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
    if ($null -eq $installed -or [string]$installed.StartName -ne $accountName) {
        throw "Service '$serviceName' is running as '$($installed.StartName)' but '$accountName' was requested."
    }
    $health = Invoke-OpsPilotHealthCheck -ServiceName $serviceName -TimeoutSeconds $HealthTimeoutSeconds `
        -MaxRetries $HealthMaxRetries -ThrowOnFailure

    return [pscustomobject]@{
        service_name = $serviceName
        status       = 'Running'
        binary_path  = $binaryPath
        account      = $accountName
        health       = $health
    }
}

function Install-OpsPilotDashboard {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [string]$DataDir = 'C:\ProgramData\OpsPilot',
        [string]$ServiceAccount = 'NT AUTHORITY\LocalService',
        [System.Management.Automation.PSCredential]$ServiceCredential,
        [string]$HostName = '127.0.0.1',
        [int]$Port = 8090,
        [int]$HealthTimeoutSeconds = 30,
        [int]$HealthMaxRetries = 5
    )

    $serviceName = $script:DashboardServiceName
    if ($Port -lt 1 -or $Port -gt 65535) {
        throw "Dashboard port must be between 1 and 65535; got $Port."
    }
    $python = Get-OpsPilotPythonPath -InstallRoot $InstallRoot
    $entryPoint = Join-Path -Path $InstallRoot -ChildPath 'run_dashboard.py'
    if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
        throw "Dashboard entry point not found: $entryPoint"
    }
    $configFile = Join-Path -Path $InstallRoot -ChildPath 'config\defaults\dashboard.yaml'
    if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
        throw "Dashboard configuration not found: $configFile"
    }
    if (-not (Test-Path -LiteralPath $DataDir -PathType Container)) {
        if ($PSCmdlet.ShouldProcess($DataDir, 'Create dashboard data directory')) {
            New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
        }
    }
    foreach ($directory in @((Join-Path -Path $DataDir -ChildPath 'dashboard'), (Join-Path -Path $DataDir -ChildPath 'logs'))) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            if ($PSCmdlet.ShouldProcess($directory, 'Create dashboard state directory')) {
                New-Item -ItemType Directory -Path $directory -Force | Out-Null
            }
        }
    }

    $binaryPath = '"{0}" "{1}" --data-dir "{2}" --config "{3}" --host "{4}" --port {5}' -f `
        $python, $entryPoint, $DataDir, $configFile, $HostName, $Port
    $existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    $accountName = $ServiceAccount
    if ($null -ne $ServiceCredential) {
        $accountName = $ServiceCredential.UserName
    }

    if (-not $PSCmdlet.ShouldProcess($serviceName, 'Register and start the OpsPilot dashboard service')) {
        return [pscustomobject]@{
            service_name = $serviceName
            status       = 'WhatIf'
            binary_path  = $binaryPath
            account      = $accountName
            url          = ('http://{0}:{1}/api/health' -f $HostName, $Port)
        }
    }

    $builtInSid = switch ($accountName) {
        'NT AUTHORITY\LocalService' { '*S-1-5-19' }
        'LocalService' { '*S-1-5-19' }
        'NT AUTHORITY\NetworkService' { '*S-1-5-20' }
        'NetworkService' { '*S-1-5-20' }
        default { $null }
    }
    if ($null -ne $builtInSid) {
        Grant-OpsPilotDirectoryAccess -Path $DataDir -Sid $builtInSid -Confirm:$false
    }

    if ($null -ne $existing) {
        if ($existing.Status.ToString() -ne 'Stopped') {
            Stop-Service -Name $serviceName -Force -ErrorAction Stop
        }
        Set-Service -Name $serviceName -BinaryPathName $binaryPath -StartupType Automatic -ErrorAction Stop
        if ($null -ne $ServiceCredential) {
            Set-Service -Name $serviceName -Credential $ServiceCredential -ErrorAction Stop
        }
        else {
            & sc.exe config $serviceName 'obj=' $accountName | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "sc.exe config failed to set the account for '$serviceName'."
            }
        }
    }
    else {
        $parameters = @{
            Name           = $serviceName
            BinaryPathName = $binaryPath
            DisplayName    = 'OpsPilot Mission Control Dashboard'
            Description    = 'OpsPilot Cloud read-only Mission Control dashboard (COMP-13).'
            StartupType    = 'Automatic'
        }
        if ($null -ne $ServiceCredential) {
            $parameters['Credential'] = $ServiceCredential
        }
        New-Service @parameters | Out-Null
        if ($null -eq $ServiceCredential) {
            & sc.exe config $serviceName 'obj=' $accountName | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "sc.exe config failed to set the account for new service '$serviceName'."
            }
        }
    }

    Start-Service -Name $serviceName -ErrorAction Stop
    $installed = Get-CimInstance -ClassName Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
    if ($null -eq $installed -or [string]$installed.StartName -ne $accountName) {
        throw "Service '$serviceName' is running as '$($installed.StartName)' but '$accountName' was requested."
    }
    if (-not (Get-Command -Name Invoke-OpsPilotHealthCheck -ErrorAction SilentlyContinue)) {
        Import-Module (Join-Path -Path $PSScriptRoot -ChildPath 'OpsPilot.Health.psm1') -Force
    }
    $url = ('http://{0}:{1}/api/health' -f $HostName, $Port)
    $health = Invoke-OpsPilotHealthCheck -Url $url -TimeoutSeconds $HealthTimeoutSeconds `
        -MaxRetries $HealthMaxRetries -ThrowOnFailure

    return [pscustomobject]@{
        service_name = $serviceName
        status       = 'Running'
        binary_path  = $binaryPath
        account      = $accountName
        url          = $url
        health       = $health
    }
}

function Invoke-OpsPilotPythonModule {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $python = Get-OpsPilotPythonPath -InstallRoot $InstallRoot
    $previous = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = (Join-Path -Path $InstallRoot -ChildPath 'src')
        Push-Location $InstallRoot
        try {
            $output = & $python @Arguments 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "Python command failed with exit code $LASTEXITCODE`: $($output | Out-String)"
            }
            return ($output | Out-String).Trim()
        }
        finally {
            Pop-Location
        }
    }
    finally {
        $env:PYTHONPATH = $previous
    }
}

function Invoke-OpsPilotCheckpoint {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$Tag,
        [string]$ServiceName = $script:ControlPlaneServiceName
    )

    $output = Invoke-OpsPilotPythonModule -InstallRoot $InstallRoot -Arguments @(
        '-m', 'orchestrator.cli', 'checkpoint',
        '--service', $ServiceName,
        '--label', $Tag
    )
    $line = ($output -split "`r?`n" | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($line)) {
        throw "Checkpoint command returned no JSON: $output"
    }
    $result = $line | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace([string]$result.checkpoint_id)) {
        throw "Checkpoint command returned no checkpoint_id: $line"
    }
    return [string]$result.checkpoint_id
}

function Invoke-OpsPilotRollback {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$CheckpointId,
        [string]$Actor = 'installer'
    )

    $output = Invoke-OpsPilotPythonModule -InstallRoot $InstallRoot -Arguments @(
        '-m', 'orchestrator.cli', 'rollback',
        '--checkpoint-id', $CheckpointId,
        '--actor', $Actor
    )
    $line = ($output -split "`r?`n" | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($line)) {
        throw "Rollback command returned no JSON: $output"
    }
    return ($line | ConvertFrom-Json)
}

function Write-OpsPilotHandoffResult {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)][string]$Phase,
        [Parameter(Mandatory = $true)][string]$Status,
        [string[]]$Details = @()
    )

    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "Handoff Manifest not found: $ManifestPath"
    }
    if (-not $PSCmdlet.ShouldProcess($ManifestPath, "Record install result for phase $Phase")) {
        return [pscustomobject]@{ path = $ManifestPath; status = 'WhatIf' }
    }

    $begin = '<!-- opspilot-install-result:begin -->'
    $end = '<!-- opspilot-install-result:end -->'
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($begin)
    $lines.Add('## Windows Install Result')
    $lines.Add("- Phase: $Phase")
    $lines.Add("- Status: $Status")
    $lines.Add("- Host: $env:COMPUTERNAME")
    $lines.Add("- Recorded: $((Get-Date).ToUniversalTime().ToString('o'))")
    foreach ($detail in $Details) {
        $lines.Add("- $detail")
    }
    $lines.Add($end)
    $block = ($lines -join [Environment]::NewLine)

    $content = Get-Content -LiteralPath $ManifestPath -Raw
    $pattern = [regex]::Escape($begin) + '.*?' + [regex]::Escape($end)
    if ($content -match $pattern) {
        $content = [regex]::Replace($content, $pattern, [System.Text.RegularExpressions.MatchEvaluator] { $block }, 'Singleline')
    }
    else {
        $content = $content.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine + $block + [Environment]::NewLine
    }
    Set-Content -LiteralPath $ManifestPath -Value $content -Encoding utf8 -NoNewline
    return [pscustomobject]@{ path = $ManifestPath; status = $Status }
}

Export-ModuleMember -Function Test-OpsPilotAdministrator, Get-OpsPilotPythonPath, Test-OpsPilotPrerequisite, Initialize-OpsPilotPythonEnvironment, Copy-OpsPilotPayload, Install-OpsPilotControlPlane, Install-OpsPilotDashboard, Invoke-OpsPilotPythonModule, Invoke-OpsPilotCheckpoint, Invoke-OpsPilotRollback, Write-OpsPilotHandoffResult
