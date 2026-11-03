# OpsPilot.Health — bounded health checks used by the installer and control plane.
# No bash, curl, or WSL is required; everything is native PowerShell/.NET.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-OpsPilotHttpProbe {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 5
    )

    $started = Get-Date
    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSeconds -UseBasicParsing -ErrorAction Stop
        $healthy = $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
        return [pscustomobject]@{
            healthy    = [bool]$healthy
            status_code = [int]$response.StatusCode
            error      = if ($healthy) { $null } else { "HTTP $($response.StatusCode)" }
            latency_ms = [int]((Get-Date) - $started).TotalMilliseconds
        }
    }
    catch {
        return [pscustomobject]@{
            healthy    = $false
            status_code = $null
            error      = $_.Exception.Message
            latency_ms = [int]((Get-Date) - $started).TotalMilliseconds
        }
    }
}

function Invoke-OpsPilotHealthCheck {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [string]$Url,
        [string]$ServiceName,
        [int]$TimeoutSeconds = 5,
        [int]$MaxRetries = 2,
        [int]$RetryDelaySeconds = 5,
        [switch]$ThrowOnFailure
    )

    if ([string]::IsNullOrWhiteSpace($Url) -eq [string]::IsNullOrWhiteSpace($ServiceName)) {
        throw 'Specify exactly one of -Url or -ServiceName.'
    }
    if ($MaxRetries -lt 0 -or $MaxRetries -gt 10) {
        throw 'MaxRetries must be between 0 and 10.'
    }
    if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 300) {
        throw 'TimeoutSeconds must be between 1 and 300.'
    }

    $attempt = 0
    $lastError = $null
    $lastStatus = $null
    do {
        $attempt++
        if (-not [string]::IsNullOrWhiteSpace($Url)) {
            $probe = Invoke-OpsPilotHttpProbe -Url $Url -TimeoutSeconds $TimeoutSeconds
            $healthy = [bool]$probe.healthy
            $lastError = $probe.error
            $lastStatus = $probe.status_code
        }
        else {
            $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
            if ($null -eq $service) {
                $healthy = $false
                $lastError = "Service '$ServiceName' does not exist."
            }
            else {
                $healthy = $service.Status.ToString() -eq 'Running'
                $lastError = if ($healthy) { $null } else { "Service status is $($service.Status)." }
            }
            $lastStatus = $null
        }
        if ($healthy) { break }
        if ($attempt -le $MaxRetries) {
            Start-Sleep -Seconds $RetryDelaySeconds
        }
    } while ($attempt -le $MaxRetries)

    $result = [pscustomobject]@{
        healthy     = [bool]$healthy
        attempts    = $attempt
        target      = if ($Url) { $Url } else { $ServiceName }
        status_code = $lastStatus
        error       = $lastError
        checked_at  = (Get-Date).ToUniversalTime().ToString('o')
    }
    if (-not $healthy -and $ThrowOnFailure.IsPresent) {
        throw "Health check failed for $($result.target) after $attempt attempt(s): $lastError"
    }
    return $result
}

Export-ModuleMember -Function Invoke-OpsPilotHttpProbe, Invoke-OpsPilotHealthCheck
