param([switch]$StopCameraGateway)
# Stop backend services. The independent camera gateway is stopped only when requested.
$ErrorActionPreference = 'Stop'
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Failures = [System.Collections.Generic.List[string]]::new()
# Serialize start/stop so a second terminal cannot restart containers mid-shutdown.
$LifecycleHash = [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($RootDir))).Replace('-', '').Substring(0, 12)
$LifecycleMutex = New-Object System.Threading.Mutex($false, "Local\MyReID-Production-$LifecycleHash")
try { $HasLifecycleLock = $LifecycleMutex.WaitOne([TimeSpan]::FromMinutes(3)) }
catch [System.Threading.AbandonedMutexException] { $HasLifecycleLock = $true }
if (!$HasLifecycleLock) { $LifecycleMutex.Dispose(); throw 'Another production start/stop is still running. Retry after it finishes.' }
Push-Location $RootDir
try {
    $ComposeArgs = @('compose', '--env-file', '.env.prod', '-p', 'myreid-production', '-f', 'docker-compose.yml', '-f', 'docker-compose.local-production.yml', '--profile', 'visitor')
    # Let the backend flush recordings before disconnecting the camera publisher.
    try {
        & docker @ComposeArgs stop -t 30 backend worker
        if ($LASTEXITCODE -ne 0) { $Failures.Add('Backend/worker shutdown failed') }
    } catch { $Failures.Add($_.Exception.Message) }
    try {
        if ($StopCameraGateway) { & (Join-Path $PSScriptRoot 'run_camera_gateway.ps1') -Stop }
    } catch { $Failures.Add($_.Exception.Message) }
    try {
        & docker @ComposeArgs stop -t 30
        if ($LASTEXITCODE -ne 0) { $Failures.Add('Docker project shutdown failed') }
    } catch { $Failures.Add($_.Exception.Message) }
    if ($Failures.Count) { throw ($Failures -join '; ') }
    Write-Host 'Backend project stopped. Data volumes preserved. The independent camera gateway remains running unless -StopCameraGateway was supplied.'
} finally {
    Pop-Location
    $LifecycleMutex.ReleaseMutex()
    $LifecycleMutex.Dispose()
}
