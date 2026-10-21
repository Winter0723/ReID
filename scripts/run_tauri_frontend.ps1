param(
    [switch]$Install,
    [switch]$BrowserOnly
)

$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendDir = Join-Path $RootDir "TauriFrontend"
$Npm = if (Get-Command "npm.cmd" -ErrorAction SilentlyContinue) { "npm.cmd" } else { "npm" }
$NpmCacheDir = Join-Path $FrontendDir ".npm-cache"

if ($env:MYREID_API_BASE_URL -and -not $env:VITE_MYREID_API_BASE_URL) {
    $env:VITE_MYREID_API_BASE_URL = $env:MYREID_API_BASE_URL
}

if (-not $env:npm_config_cache) {
    $env:npm_config_cache = $NpmCacheDir
}
New-Item -ItemType Directory -Force -Path $env:npm_config_cache | Out-Null

Push-Location $FrontendDir
try {
    if ($Install -or -not (Test-Path -LiteralPath (Join-Path $FrontendDir "node_modules"))) {
        & $Npm install
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }

    if ($BrowserOnly) {
        & $Npm run dev
    } else {
        & $Npm run tauri:dev
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
