#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/FastAPIBackend"
VENV_PY="$BACKEND_DIR/venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Python virtual environment not found at $BACKEND_DIR/venv" >&2
  echo "Create it with: cd FastAPIBackend && python -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

# Parse dotenv as data: JSON camera mappings and font names are not shell syntax.
exec "$VENV_PY" - "$ROOT_DIR" <<'PY'
import os
from pathlib import Path
import sys
from dotenv import dotenv_values

root = Path(sys.argv[1])
backend = root / "FastAPIBackend"
values = {}
for path in (root / ".env", backend / ".env", root / ".env.local", backend / ".env.local"):
    values.update(dotenv_values(path))
for key, value in values.items():
    if value is not None:
        os.environ.setdefault(key, value)
os.chdir(backend)
args = [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
        "--port", os.getenv("MYREID_API_PORT", "2889")]
if os.getenv("MYREID_RELOAD") == "1":
    args.append("--reload")
os.execv(sys.executable, args)
PY
