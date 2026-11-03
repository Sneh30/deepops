# OpsPilot.Secrets — DPAPI-backed local secret storage (COMP-11, NFR-009).
# Encrypted blobs are tied to the Windows/service account that wrote them.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:DefaultRoot = 'C:\ProgramData\OpsPilot\secrets'

function Assert-OpsPilotSecretName {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Name)
    if ($Name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
        throw "Invalid secret name: $Name"
    }
}

function Initialize-OpsPilotSecretRoot {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Root)

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        New-Item -ItemType Directory -Path $Root -Force | Out-Null
    }
    # Restrict to SYSTEM and the local Administrators group by well-known SID,
    # avoiding localized group-name assumptions.
    & icacls.exe $Root /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to restrict ACLs on secret root: $Root"
    }
}

function Get-OpsPilotSecretPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Root = $script:DefaultRoot
    )
    Assert-OpsPilotSecretName -Name $Name
    return (Join-Path -Path $Root -ChildPath "$Name.secret")
}

function Set-OpsPilotSecret {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$ValueBase64,
        [string]$Root = $script:DefaultRoot
    )

    Assert-OpsPilotSecretName -Name $Name
    $plain = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($ValueBase64))
    if ([string]::IsNullOrEmpty($plain)) {
        throw 'Secret value must not be empty.'
    }

    if (-not $PSCmdlet.ShouldProcess($Name, 'Store DPAPI-encrypted secret')) {
        return [pscustomobject]@{ name = $Name; stored = $false; whatif = $true }
    }

    Initialize-OpsPilotSecretRoot -Root $Root
    $secure = [System.Security.SecureString]::new()
    foreach ($character in $plain.ToCharArray()) {
        $secure.AppendChar($character)
    }
    $secure.MakeReadOnly()
    # No -Key: DPAPI user scope, tied to the account running the control plane.
    $encrypted = ConvertFrom-SecureString -SecureString $secure
    $path = Get-OpsPilotSecretPath -Name $Name -Root $Root
    Set-Content -LiteralPath $path -Value $encrypted -Encoding utf8 -NoNewline
    return [pscustomobject]@{ name = $Name; stored = $true; whatif = $false }
}

function Get-OpsPilotSecret {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Root = $script:DefaultRoot
    )

    $path = Get-OpsPilotSecretPath -Name $Name -Root $Root
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return [pscustomobject]@{ name = $Name; found = $false; secret_base64 = $null }
    }

    $encrypted = Get-Content -LiteralPath $path -Raw
    $secure = ConvertTo-SecureString -String $encrypted
    $credential = New-Object System.Management.Automation.PSCredential('opspilot', $secure)
    $plain = $credential.GetNetworkCredential().Password
    $encoded = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($plain))
    return [pscustomobject]@{ name = $Name; found = $true; secret_base64 = $encoded }
}

function Remove-OpsPilotSecret {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Root = $script:DefaultRoot
    )

    $path = Get-OpsPilotSecretPath -Name $Name -Root $Root
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return [pscustomobject]@{ name = $Name; removed = $false }
    }
    if (-not $PSCmdlet.ShouldProcess($Name, 'Delete secret')) {
        return [pscustomobject]@{ name = $Name; removed = $false }
    }
    Remove-Item -LiteralPath $path -Force
    return [pscustomobject]@{ name = $Name; removed = $true }
}

function Get-OpsPilotSecretList {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param([string]$Root = $script:DefaultRoot)

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return [pscustomobject]@{ names = @() }
    }
    $names = @(Get-ChildItem -LiteralPath $Root -Filter '*.secret' -File |
        ForEach-Object { $_.BaseName })
    return [pscustomobject]@{ names = $names }
}

Export-ModuleMember -Function Set-OpsPilotSecret, Get-OpsPilotSecret, Remove-OpsPilotSecret, Get-OpsPilotSecretList
