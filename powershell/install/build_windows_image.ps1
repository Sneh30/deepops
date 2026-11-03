<#
.SYNOPSIS
    Build a Windows-container image for OpsPilot (IMPL-201).

.DESCRIPTION
    Fails closed unless Docker is in Windows-container mode and the Dockerfile
    uses an mcr.microsoft.com/windows/* base. Never invokes WSL or a Linux
    container runtime (ADR-003).
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Image,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Tag,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Context,

    [string]$Dockerfile = 'Dockerfile.windows'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Tag -eq 'latest') {
    throw 'Refusing to build an unpinned :latest image. Pass an immutable version tag.'
}
if (-not (Test-Path -LiteralPath $Context -PathType Container)) {
    throw "Build context not found: $Context"
}
$dockerfilePath = Join-Path -Path $Context -ChildPath $Dockerfile
if (-not (Test-Path -LiteralPath $dockerfilePath -PathType Leaf)) {
    throw "Dockerfile not found: $dockerfilePath"
}

$content = Get-Content -LiteralPath $dockerfilePath -Raw
if ($content -notmatch 'mcr\.microsoft\.com/windows/(servercore|nanoserver):') {
    throw 'Dockerfile must use an mcr.microsoft.com/windows/servercore or nanoserver base.'
}

$osType = (& docker info --format '{{.OSType}}' 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw 'docker info failed; Docker Engine must be installed and running.'
}
if ($osType -ne 'windows') {
    throw "Docker is in '$osType' mode. Build requires Windows-container mode; Linux-container fallback is forbidden."
}

$fullTag = "${Image}:$Tag"
if (-not $PSCmdlet.ShouldProcess($fullTag, 'Build Windows container image')) {
    return [pscustomobject]@{
        image      = $fullTag
        dockerfile = $dockerfilePath
        status     = 'WhatIf'
    }
}

& docker build --isolation=process --tag $fullTag --file $dockerfilePath $Context
if ($LASTEXITCODE -ne 0) {
    throw "docker build failed for $fullTag with exit code $LASTEXITCODE."
}

$inspect = (& docker image inspect $fullTag --format '{{.Os}}' 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $inspect -ne 'windows') {
    throw "Built image $fullTag does not report OS=windows."
}

return [pscustomobject]@{
    image      = $fullTag
    dockerfile = $dockerfilePath
    os         = $inspect
    status     = 'Built'
}
