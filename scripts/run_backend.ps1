param(
    [switch]$CreateVenv,
    [switch]$Install,
    [switch]$Realtime,
    [int]$Port = 0,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendDir = Join-Path $RootDir "FastAPIBackend"
$VenvPython = Join-Path $BackendDir "venv\Scripts\python.exe"

function Import-DotEnv {
    param(
        [string]$Path,
        [hashtable]$ProtectedKeys
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $rawLine.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith("#")) {
            continue
        }
        if ($line.StartsWith("export ")) {
            $line = $line.Substring(7).Trim()
        }

        $separatorIndex = $line.IndexOf("=")
        if ($separatorIndex -lt 1) {
            continue
        }

        $name = $line.Substring(0, $separatorIndex).Trim()
        if ($ProtectedKeys.ContainsKey($name)) {
            continue
        }

        $value = $line.Substring($separatorIndex + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value[0]
            $last = $value[$value.Length - 1]
            if (($first -eq "'" -and $last -eq "'") -or ($first -eq '"' -and $last -eq '"')) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

$protectedKeys = @{}
foreach ($key in [Environment]::GetEnvironmentVariables("Process").Keys) {
    $protectedKeys[$key] = $true
}

foreach ($envFile in @(
    (Join-Path $RootDir ".env"),
    (Join-Path $BackendDir ".env"),
    (Join-Path $RootDir ".env.local"),
    (Join-Path $BackendDir ".env.local")
)) {
    Import-DotEnv -Path $envFile -ProtectedKeys $protectedKeys
}

if ($CreateVenv -and -not (Test-Path -LiteralPath $VenvPython)) {
    $basePython = if ($Python) { $Python } else { "python" }
    & $basePython -m venv (Join-Path $BackendDir "venv")
}

if (-not $Python) {
    if (Test-Path -LiteralPath $VenvPython) {
        $Python = $VenvPython
    } else {
        $Python = "python"
    }
}

if ($Install) {
    & $Python -m pip install -r (Join-Path $BackendDir "requirements.txt")
}

if ($Realtime) {
    & $Python -c "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "Realtime GPU mode requires a CUDA-enabled PyTorch runtime. The current runtime has no available CUDA device."
    }
    $env:MODEL_DEVICE = "cuda"
}

if ($Port -eq 0) {
    $Port = if ($env:MYREID_API_PORT) { [int]$env:MYREID_API_PORT } else { 2889 }
}
if ($Port -lt 1 -or $Port -gt 65535) { throw "MYREID_API_PORT must be between 1 and 65535." }
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
& $Python -c "import socket,sys; s=socket.socket(); s.bind(('127.0.0.1',int(sys.argv[1]))); s.close()" $Port
if ($LASTEXITCODE -ne 0) {
    throw "Backend port $Port cannot be bound (busy or reserved by Windows). Release the configured port and retry. The normal API port is fixed at 2889; do not change the frontend proxy or select another port."
}
Write-Host "[MyReID] Starting backend at http://127.0.0.1:$Port"
$uvicornArgs = @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", [string]$Port)
if ($env:MYREID_RELOAD -eq "1") {
    $uvicornArgs += "--reload"
}

Push-Location $BackendDir
try {
    & $Python @uvicornArgs
    $backendExitCode = $LASTEXITCODE
    if ($backendExitCode -ne 0) { Write-Warning "Backend exited with code $backendExitCode. See the error above." }
    exit $backendExitCode
}
finally {
    Pop-Location
}
