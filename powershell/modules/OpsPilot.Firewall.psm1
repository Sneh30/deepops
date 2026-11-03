# OpsPilot.Firewall — named firewall rule lifecycle with checkpoint restore.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-OpsPilotFirewallRule {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Name
    )

    $rule = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $rule) {
        return [pscustomobject]@{
            exists         = $false
            name           = $Name
            enabled        = $false
            direction      = $null
            action         = $null
            protocol       = $null
            local_port     = $null
            remote_address = $null
            profile        = $null
        }
    }

    $portFilter = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule -ErrorAction SilentlyContinue
    $addressFilter = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule -ErrorAction SilentlyContinue
    $applicationFilter = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule -ErrorAction SilentlyContinue
    $serviceFilter = Get-NetFirewallServiceFilter -AssociatedNetFirewallRule $rule -ErrorAction SilentlyContinue

    $protocol = $null
    $localPort = $null
    if ($null -ne $portFilter) {
        $protocol = [string]$portFilter.Protocol
        $localPort = [string]$portFilter.LocalPort
    }
    $remoteAddress = $null
    if ($null -ne $addressFilter) {
        $remoteAddress = ([string[]]$addressFilter.RemoteAddress) -join ','
    }

    return [pscustomobject]@{
        exists         = $true
        name           = [string]$rule.DisplayName
        enabled        = [string]$rule.Enabled -eq 'True'
        direction      = [string]$rule.Direction
        action         = [string]$rule.Action
        protocol       = $protocol
        local_port     = $localPort
        remote_address = $remoteAddress
        profile        = [string]$rule.Profile
        program        = if ($null -ne $applicationFilter) { [string]$applicationFilter.Program } else { $null }
        service        = if ($null -ne $serviceFilter) { [string]$serviceFilter.Service } else { $null }
    }
}

function New-OpsPilotFirewallRule {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [ValidateSet('Inbound', 'Outbound')]
        [string]$Direction,
        [Parameter(Mandatory = $true)]
        [ValidateSet('Allow', 'Block')]
        [string]$Action,
        [Parameter(Mandatory = $true)]
        [ValidateSet('TCP', 'UDP')]
        [string]$Protocol,
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$LocalPort,
        [string]$RemoteAddress = 'Any',
        [Alias('Profile')]
        [ValidateSet('Any', 'Domain', 'Private', 'Public')]
        [string]$FirewallProfile = 'Any'
    )

    if (-not $PSCmdlet.ShouldProcess($Name, "Create/update firewall rule $Direction $Action $Protocol/$LocalPort")) {
        return [pscustomobject]@{
            name      = $Name
            direction = $Direction
            action    = $Action
            enabled   = $false
            local_port = $LocalPort
        }
    }

    $safeName = ($Name -replace '[^A-Za-z0-9_.-]', '_')
    Remove-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue

    $parameters = @{
        DisplayName    = $Name
        Name           = $safeName
        Direction      = $Direction
        Action         = $Action
        Protocol       = $Protocol
        LocalPort      = $LocalPort
        RemoteAddress  = $RemoteAddress
        Profile        = $FirewallProfile
        Enabled        = 'True'
        ErrorAction    = 'Stop'
    }
    if ($Direction -eq 'Inbound') {
        $parameters['RemoteAddress'] = $RemoteAddress
    }
    New-NetFirewallRule @parameters | Out-Null

    return Get-OpsPilotFirewallRule -Name $Name
}

function Remove-OpsPilotFirewallRule {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Name
    )

    $existing = Get-OpsPilotFirewallRule -Name $Name
    if (-not $existing.exists) {
        return [pscustomobject]@{ removed = $false; name = $Name }
    }
    if (-not $PSCmdlet.ShouldProcess($Name, 'Remove firewall rule')) {
        return [pscustomobject]@{ removed = $false; name = $Name }
    }
    Remove-NetFirewallRule -DisplayName $Name -ErrorAction Stop
    return [pscustomobject]@{ removed = $true; name = $Name }
}

function Restore-OpsPilotFirewallRuleState {
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

    if (-not $PSCmdlet.ShouldProcess($name, 'Restore firewall rule state')) {
        return Get-OpsPilotFirewallRule -Name $name
    }

    Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if ([bool]$state.exists) {
        $parameters = @{
            DisplayName   = $name
            Name          = ($name -replace '[^A-Za-z0-9_.-]', '_')
            Direction     = [string]$state.direction
            Action        = [string]$state.action
            Protocol      = [string]$state.protocol
            LocalPort     = [string]$state.local_port
            RemoteAddress = [string]$state.remote_address
            Profile       = [string]$state.profile
            Enabled       = [string]$state.enabled
            ErrorAction   = 'Stop'
        }
        New-NetFirewallRule @parameters | Out-Null
    }

    return Get-OpsPilotFirewallRule -Name $name
}

Export-ModuleMember -Function Get-OpsPilotFirewallRule, New-OpsPilotFirewallRule, Remove-OpsPilotFirewallRule, Restore-OpsPilotFirewallRuleState
