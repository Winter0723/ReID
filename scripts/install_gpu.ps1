$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonExe = Join-Path $RootDir "FastAPIBackend/venv/Scripts/python.exe"
& $PythonExe -m pip install --upgrade --no-deps torch==2.12.0+cu130 torchvision==0.27.0+cu130 --index-url https://download.pytorch.org/whl/cu130
if ($LASTEXITCODE -ne 0) { throw "CUDA PyTorch installation failed" }
& $PythonExe -c "import torch; assert torch.cuda.is_available(), 'CUDA unavailable'; print(torch.__version__, torch.cuda.get_device_name(0)); print(torch.ones(1, device='cuda').item())"
if ($LASTEXITCODE -ne 0) { throw "CUDA verification failed" }
