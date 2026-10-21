#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/FastAPIBackend/venv/bin/python}"

cd "$ROOT_DIR"

"$PYTHON_BIN" -m pytest
"$PYTHON_BIN" scripts/smoke_backend.py
"$PYTHON_BIN" scripts/check_openapi_docs.py

if [[ "${MYREID_RUN_HTTP_SMOKE:-0}" == "1" ]]; then
  "$PYTHON_BIN" scripts/smoke_business_http.py "$@"
else
  echo "live business HTTP smoke skipped; set MYREID_RUN_HTTP_SMOKE=1 to run it against a running backend"
fi
