# OpsPilot.Baseline — security baseline apply, test, and checkpoint restore.
# Mirrors ENGINEERING_REFERENCE.md §5.2. Mutating function supports -WhatIf.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RdpRegistryPath = 'HKLM:\System\CurrentControlSet\Control\Terminal Server'
$script:TlsProtocols = @('TLS 1.0', 'TLS 1.1', 'TLS 1.2')
$script:TlsRoles = @('Server', 'Client')
$script:MetricsPort = 9182

function Get-OpsPilotRegistryValue {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $item = Get-ItemProperty -Path $Path -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $null }
    $property = $item.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Set-OpsPilotRegistryValue {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$Value
    )
    if (-not $PSCmdlet.ShouldProcess($Path, "Set $Name=$Value")) {
        return
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -Path $Path -Force | Out-Null
    }
    New-ItemProperty -Path $Path -Name $Name -Value $Value -PropertyType DWord -Force | Out-Null
}

function Get-OpsPilotBaselineState {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param()

    $profiles = @(Get-NetFirewallProfile | ForEach-Object {
        [pscustomobject]@{
            name             = [string]$_.Name
            enabled          = [string]$_.Enabled -eq 'True'
            default_inbound  = [string]$_.DefaultInboundAction
            default_outbound = [string]$_.DefaultOutboundAction
        }
    })

    $rdpDenied = Get-OpsPilotRegistryValue -Path $script:RdpRegistryPath -Name 'fDenyTSConnections'
    $rdpEnabled = $null -ne $rdpDenied -and [int]$rdpDenied -eq 0

    $tlsValues = @()
    foreach ($protocol in $script:TlsProtocols) {
        foreach ($role in $script:TlsRoles) {
            $path = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\$protocol\$role"
            $tlsValues += [pscustomobject]@{
                protocol = $protocol
                role     = $role
                path     = $path
                enabled  = Get-OpsPilotRegistryValue -Path $path -Name 'Enabled'
                disabled = Get-OpsPilotRegistryValue -Path $path -Name 'DisabledByDefault'
            }
        }
    }

    $managedRules = @(Get-NetFirewallRule -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like 'OpsPilot-*' } |
        ForEach-Object {
            $rule = Get-OpsPilotFirewallRule -Name ([string]$_.DisplayName)
            [pscustomobject]@{
                name           = $rule.name
                direction      = $rule.direction
                action         = $rule.action
                protocol       = $rule.protocol
                local_port     = $rule.local_port
                remote_address = $rule.remote_address
                profile        = $rule.profile
                enabled        = $rule.enabled
            }
        })

    return [pscustomobject]@{
        firewall_profiles = $profiles
        rdp_enabled       = $rdpEnabled
        tls               = $tlsValues
        managed_rules     = $managedRules
    }
}

function Set-OpsPilotBaseline {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [int[]]$AllowedInboundTcpPorts,
        [Parameter(Mandatory = $true)]
        [string[]]$MetricsScrapeCidrs,
        [Parameter(Mandatory = $true)]
        [bool]$RdpEnabled,
        [string]$ServiceAccount = ''
    )

    if (-not $PSCmdlet.ShouldProcess('Windows host', 'Apply OpsPilot security baseline')) {
        return [pscustomobject]@{
            applied   = $false
            compliant = $false
            findings  = @('WhatIf: no changes applied')
        }
    }

    Set-NetFirewallProfile -Profile Domain, Private, Public -Enabled True `
        -DefaultInboundAction Block -DefaultOutboundAction Allow -ErrorAction Stop

    Get-NetFirewallRule -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like 'OpsPilot-*' } |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue

    foreach ($port in $AllowedInboundTcpPorts) {
        $name = "OpsPilot-Allow-$port"
        New-NetFirewallRule -DisplayName $name -Name ($name -replace '[^A-Za-z0-9_.-]', '_') `
            -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port `
            -RemoteAddress Any -Profile Any -Enabled True -ErrorAction Stop | Out-Null
    }

    if ($MetricsScrapeCidrs.Count -gt 0) {
        New-NetFirewallRule -DisplayName 'OpsPilot-Metrics-9182' -Name 'OpsPilot-Metrics-9182' `
            -Direction Inbound -Action Allow -Protocol TCP -LocalPort $script:MetricsPort `
            -RemoteAddress $MetricsScrapeCidrs -Profile Any -Enabled True -ErrorAction Stop | Out-Null
    }

    $rdpValue = if ($RdpEnabled) { 0 } else { 1 }
    Set-OpsPilotRegistryValue -Path $script:RdpRegistryPath -Name 'fDenyTSConnections' -Value $rdpValue

    foreach ($protocol in @('TLS 1.0', 'TLS 1.1')) {
        foreach ($role in $script:TlsRoles) {
            $path = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\$protocol\$role"
            Set-OpsPilotRegistryValue -Path $path -Name 'Enabled' -Value 0
            Set-OpsPilotRegistryValue -Path $path -Name 'DisabledByDefault' -Value 1
        }
    }
    foreach ($role in $script:TlsRoles) {
        $path = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\TLS 1.2\$role"
        Set-OpsPilotRegistryValue -Path $path -Name 'Enabled' -Value 1
        Set-OpsPilotRegistryValue -Path $path -Name 'DisabledByDefault' -Value 0
    }

    $test = Test-OpsPilotBaseline -AllowedInboundTcpPorts $AllowedInboundTcpPorts `
        -MetricsScrapeCidrs $MetricsScrapeCidrs -RdpEnabled $RdpEnabled

    return [pscustomobject]@{
        applied   = $true
        compliant = [bool]$test.compliant
        findings  = @($test.findings)
        account   = $ServiceAccount
    }
}

function Test-OpsPilotBaseline {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [int[]]$AllowedInboundTcpPorts,
        [Parameter(Mandatory = $true)]
        [string[]]$MetricsScrapeCidrs,
        [Parameter(Mandatory = $true)]
        [bool]$RdpEnabled
    )

    $findings = New-Object System.Collections.Generic.List[string]

    $profiles = @(Get-NetFirewallProfile -ErrorAction Stop)
    foreach ($firewallProfile in $profiles) {
        if ([string]$firewallProfile.Enabled -ne 'True') {
            $findings.Add("Firewall profile $($firewallProfile.Name) is not enabled.")
        }
        if ([string]$firewallProfile.DefaultInboundAction -ne 'Block') {
            $findings.Add("Firewall profile $($firewallProfile.Name) does not default-deny inbound.")
        }
    }

    foreach ($port in $AllowedInboundTcpPorts) {
        $rule = Get-OpsPilotFirewallRule -Name "OpsPilot-Allow-$port"
        if (-not $rule.exists -or -not $rule.enabled) {
            $findings.Add("Missing or disabled inbound allow rule for TCP $port.")
        }
    }

    if ($MetricsScrapeCidrs.Count -gt 0) {
        $metricsRule = Get-OpsPilotFirewallRule -Name 'OpsPilot-Metrics-9182'
        if (-not $metricsRule.exists -or -not $metricsRule.enabled) {
            $findings.Add('Missing or disabled metrics scrape rule on TCP 9182.')
        }
    }

    $rdpDenied = Get-OpsPilotRegistryValue -Path $script:RdpRegistryPath -Name 'fDenyTSConnections'
    $actualRdpEnabled = $null -ne $rdpDenied -and [int]$rdpDenied -eq 0
    if ($actualRdpEnabled -ne $RdpEnabled) {
        $findings.Add("RDP enabled state is $actualRdpEnabled but expected $RdpEnabled.")
    }

    $tls12Path = 'HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\TLS 1.2\Server'
    if ((Get-OpsPilotRegistryValue -Path $tls12Path -Name 'Enabled') -ne 1) {
        $findings.Add('TLS 1.2 server protocol is not enabled.')
    }
    foreach ($protocol in @('TLS 1.0', 'TLS 1.1')) {
        foreach ($role in $script:TlsRoles) {
            $path = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\$protocol\$role"
            if ((Get-OpsPilotRegistryValue -Path $path -Name 'Enabled') -eq 1) {
                $findings.Add("$protocol $role is still enabled.")
            }
        }
    }

    return [pscustomobject]@{
        compliant = ($findings.Count -eq 0)
        findings  = @($findings)
    }
}

function Restore-OpsPilotBaselineState {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$StateBase64
    )

    $json = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($StateBase64))
    $state = $json | ConvertFrom-Json

    if (-not $PSCmdlet.ShouldProcess('Windows host', 'Restore checkpointed baseline state')) {
        return Get-OpsPilotBaselineState
    }

    foreach ($firewallProfile in @($state.firewall_profiles)) {
        Set-NetFirewallProfile -Name ([string]$firewallProfile.name) -Enabled ([bool]$firewallProfile.enabled) `
            -DefaultInboundAction ([string]$firewallProfile.default_inbound) `
            -DefaultOutboundAction ([string]$firewallProfile.default_outbound) -ErrorAction Stop
    }

    Get-NetFirewallRule -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like 'OpsPilot-*' } |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue

    foreach ($rule in @($state.managed_rules)) {
        if ($null -eq $rule) { continue }
        New-NetFirewallRule -DisplayName ([string]$rule.name) `
            -Name (([string]$rule.name) -replace '[^A-Za-z0-9_.-]', '_') `
            -Direction ([string]$rule.direction) -Action ([string]$rule.action) `
            -Protocol ([string]$rule.protocol) -LocalPort ([string]$rule.local_port) `
            -RemoteAddress ([string]$rule.remote_address) -Profile ([string]$rule.profile) `
            -Enabled ([bool]$rule.enabled) -ErrorAction Stop | Out-Null
    }

    $rdpValue = if ([bool]$state.rdp_enabled) { 0 } else { 1 }
    Set-OpsPilotRegistryValue -Path $script:RdpRegistryPath -Name 'fDenyTSConnections' -Value $rdpValue

    foreach ($tls in @($state.tls)) {
        if ($null -eq $tls.enabled -and $null -eq $tls.disabled) {
            Remove-ItemProperty -Path ([string]$tls.path) -Name 'Enabled' -ErrorAction SilentlyContinue
            Remove-ItemProperty -Path ([string]$tls.path) -Name 'DisabledByDefault' -ErrorAction SilentlyContinue
        }
        else {
            Set-OpsPilotRegistryValue -Path ([string]$tls.path) -Name 'Enabled' -Value ([int]$tls.enabled)
            Set-OpsPilotRegistryValue -Path ([string]$tls.path) -Name 'DisabledByDefault' -Value ([int]$tls.disabled)
        }
    }

    return Get-OpsPilotBaselineState
}

Export-ModuleMember -Function Get-OpsPilotBaselineState, Set-OpsPilotBaseline, Test-OpsPilotBaseline, Restore-OpsPilotBaselineState
