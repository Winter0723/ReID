param([switch]$Stop)
$ErrorActionPreference = 'Stop'
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ComposeArgs = @('compose', '-p', 'myreid-camera', '-f', 'docker-compose.camera-gateway.yml')
Push-Location $RootDir
try {
    if ($Stop) {
        & (Join-Path $PSScriptRoot 'run_camera_bridge.ps1') -Stop
        & docker @ComposeArgs stop -t 10
        if ($LASTEXITCODE -ne 0) { throw 'Camera gateway shutdown failed' }
        return
    }
    # Migrate only this repository's previous production MediaMTX container.
    $Legacy = & docker ps -a --filter 'name=^myreid-production-mediamtx-1$' --filter 'label=com.docker.compose.project=myreid-production' --filter 'label=com.docker.compose.service=mediamtx' --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0) { throw 'Docker is unavailable' }
    if ($Legacy -eq 'myreid-production-mediamtx-1') {
        & (Join-Path $PSScriptRoot 'run_camera_bridge.ps1') -Stop
        & docker stop -t 10 myreid-production-mediamtx-1
        if ($LASTEXITCODE -ne 0) { throw 'Legacy gateway stop failed' }
        & docker rm myreid-production-mediamtx-1
        if ($LASTEXITCODE -ne 0) { throw 'Legacy gateway migration failed' }
    }
    & python -m camera_gateway init
    if ($LASTEXITCODE -ne 0) { throw 'Camera source discovery/configuration failed' }
    & docker @ComposeArgs up -d
    if ($LASTEXITCODE -ne 0) { throw 'Camera gateway startup failed' }
    & (Join-Path $PSScriptRoot 'run_camera_bridge.ps1')
} finally {
    Pop-Location
}
