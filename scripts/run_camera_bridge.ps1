param([switch]$Stop)
$ErrorActionPreference = 'Stop'
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Get-Command python -ErrorAction Stop).Source
$Agent = Join-Path $PSScriptRoot 'camera_bridge.py'
$RootHash = [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($RootDir))).Replace('-', '').Substring(0, 12)
$TaskName = "MyReID-Camera-$RootHash"
# Migrate away from Windows login capture: the gateway has an explicit lifecycle.
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name $TaskName -ErrorAction SilentlyContinue
if ($Stop) {
    & $Python $Agent stop
    if ($LASTEXITCODE -ne 0) { throw 'Camera agent stop failed' }
    for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
        & $Python $Agent stopped
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    throw 'Camera processes did not stop; inspect agent.log'
}
Get-Command ffmpeg -ErrorAction Stop | Out-Null
& $Python $Agent init
if ($LASTEXITCODE -ne 0) { throw 'Camera bridge initialization failed' }
$Runtime = if ($env:CAMERA_GATEWAY_DIR) { $env:CAMERA_GATEWAY_DIR } else { Join-Path $RootDir 'tmp/camera-bridge' }
# DirectShow runs in the logged-in user's session; no administrator task is required.
$PythonWindowless = Join-Path (Split-Path $Python) 'pythonw.exe'
if (!(Test-Path -LiteralPath $PythonWindowless)) { throw 'pythonw.exe is required alongside python.exe' }
$StopFile = Join-Path $Runtime 'stop'
if (Test-Path -LiteralPath $StopFile) { Remove-Item -LiteralPath $StopFile }
$StartedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
Start-Process -FilePath $PythonWindowless -ArgumentList @('"' + $Agent + '"', 'supervise') -WorkingDirectory $RootDir -WindowStyle Hidden | Out-Null
for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
    Start-Sleep -Seconds 2
    $RegistryPath = Join-Path $Runtime 'registry.json'
    if (Test-Path -LiteralPath $RegistryPath) {
        $Registry = Get-Content -Raw -LiteralPath $RegistryPath | ConvertFrom-Json
        $Age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $Registry.updated_at
        if ($Age -lt 10 -and $Registry.updated_at -ge $StartedAt -and $Registry.pid) {
            $Registry.devices | Select-Object label, source, online, status | Format-Table
            return
        }
    }
}
throw 'Camera agent heartbeat missing. Inspect tmp/camera-bridge/agent.log and supervisor.log.'
