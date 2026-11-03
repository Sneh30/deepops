# PSScriptAnalyzer gate for PowerShell sources (IMPLEMENTATION.md §3.1, §7).
[CmdletBinding()]
param(
    [string[]]$Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $Path -or $Path.Count -eq 0) {
    $repoRoot = Split-Path -Path $PSScriptRoot -Parent
    $Path = @(
        (Join-Path -Path $PSScriptRoot -ChildPath '.')
        (Join-Path -Path $repoRoot -ChildPath 'src')
    )
}

$module = Get-Module -ListAvailable -Name PSScriptAnalyzer |
    Sort-Object -Property Version -Descending |
    Select-Object -First 1
if ($null -eq $module) {
    throw 'PSScriptAnalyzer is not installed. Install the pinned version from powershell/modules/RequiredModules.psd1.'
}

$settingsPath = Join-Path -Path $PSScriptRoot -ChildPath 'PSScriptAnalyzerSettings.psd1'
$results = @()
foreach ($item in $Path) {
    if (-not (Test-Path -LiteralPath $item)) {
        throw "Path not found for PSScriptAnalyzer: $item"
    }
    $results += Invoke-ScriptAnalyzer -Path $item -Recurse -Settings $settingsPath
}

if ($results.Count -gt 0) {
    $results | Format-Table -AutoSize RuleName, Severity, ScriptName, Line, Message | Out-String | Write-Host
    throw "PSScriptAnalyzer found $($results.Count) issue(s)."
}

Write-Host 'PSScriptAnalyzer: clean'
