$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath ".env.visitor-test")) {
        $settingsText = Get-Content -Raw -LiteralPath ".env.visitor-test.example"
        $invite = [Guid]::NewGuid().ToString("N")
        $secret = [Guid]::NewGuid().ToString("N") + [Guid]::NewGuid().ToString("N")
        $settingsText = $settingsText.Replace("VISITOR_TEST_INVITE_CODE=", "VISITOR_TEST_INVITE_CODE=$invite")
        $settingsText = $settingsText.Replace("SECRET_KEY=change-me-before-production", "SECRET_KEY=$secret")
        [IO.File]::WriteAllText((Join-Path $projectRoot ".env.visitor-test"), $settingsText)
    }
    docker compose --env-file .env.visitor-test -p myreid-visitor-test -f docker-compose.yml -f docker-compose.visitor-test.yml --profile visitor up -d --build
    if ($LASTEXITCODE -ne 0) { throw "Visitor integration stack failed to start" }
    Write-Host "Visitor: http://127.0.0.1:25174/visitor/#/sites/<project-id>"
    Write-Host "Staff API: http://127.0.0.1:22889; invite code: VISITOR_TEST_INVITE_CODE in .env.visitor-test"
} finally {
    Pop-Location
}
