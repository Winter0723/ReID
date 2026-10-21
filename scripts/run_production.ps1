param([switch]$Build)
$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
# Serialize start/stop so a second terminal cannot restart containers mid-shutdown.
$LifecycleHash = [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($RootDir))).Replace('-', '').Substring(0, 12)
$LifecycleMutex = New-Object System.Threading.Mutex($false, "Local\MyReID-Production-$LifecycleHash")
try { $HasLifecycleLock = $LifecycleMutex.WaitOne([TimeSpan]::FromMinutes(3)) }
catch [System.Threading.AbandonedMutexException] { $HasLifecycleLock = $true }
if (!$HasLifecycleLock) { $LifecycleMutex.Dispose(); throw 'Another production start/stop is still running. Retry after it finishes.' }
Push-Location $RootDir
try {
    if (!(Test-Path -LiteralPath ".env.prod")) { throw "Configure .env.prod before starting production (see docs/local-production.md)." }
    $ComposeArgs = @("compose", "--env-file", ".env.prod", "-p", "myreid-production", "-f", "docker-compose.yml", "-f", "docker-compose.local-production.yml", "--profile", "visitor")
    # Validate the merged environment before building or starting any services.
    # Keep the resolved configuration in memory: it contains private credentials.
    $ResolvedJson = (& docker @ComposeArgs config --format json) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "Could not resolve deployment configuration." }
    $ResolvedConfig = $ResolvedJson | ConvertFrom-Json
    foreach ($ServiceName in @("backend", "worker", "visitor-coordinator", "maintenance")) {
        $ServiceEnv = $ResolvedConfig.services.$ServiceName.environment
        if (!$ServiceEnv) { continue }
        $RuntimeMode = ([string]$ServiceEnv.APP_ENV).Trim().ToLowerInvariant()
        $CookieSecure = ([string]$ServiceEnv.VISITOR_COOKIE_SECURE).Trim().ToLowerInvariant()
        $LanHttp = ([string]$ServiceEnv.VISITOR_ALLOW_LAN_HTTP).Trim().ToLowerInvariant()
        if ([string]$ServiceEnv.VISITOR_AUTH_PROVIDER -eq "browser" -and $RuntimeMode -notin @("local", "dev", "development", "test")) {
            throw "$ServiceName configuration conflict: VISITOR_AUTH_PROVIDER=browser requires APP_ENV=local/dev/development/test."
        }
        if ($RuntimeMode -in @("production", "prod", "staging") -and
            ($CookieSecure -in @("0", "false", "no", "off") -or $LanHttp -in @("1", "true", "yes", "on"))) {
            throw "$ServiceName configuration conflict: production requires VISITOR_COOKIE_SECURE=1 and VISITOR_ALLOW_LAN_HTTP=0. For phone testing over LAN HTTP, use an explicit local runtime configuration; changing only the cookie flags will break HTTP login."
        }
    }
    & (Join-Path $PSScriptRoot "run_camera_gateway.ps1")
    # Backend and worker share myreid:production-gpu; build that image once.
    if ($Build) { & docker @ComposeArgs build backend visitor-web; if ($LASTEXITCODE -ne 0) { throw "Production image build failed" } }
    & docker @ComposeArgs up -d --no-build
    if ($LASTEXITCODE -ne 0) { throw "Production startup failed; inspect docker compose logs" }
    & docker @ComposeArgs ps
} finally {
    Pop-Location
    $LifecycleMutex.ReleaseMutex()
    $LifecycleMutex.Dispose()
}
