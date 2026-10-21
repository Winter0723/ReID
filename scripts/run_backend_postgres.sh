#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/FastAPIBackend"
VENV_PY="$BACKEND_DIR/venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Python virtual environment not found at $BACKEND_DIR/venv" >&2
  exit 1
fi

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://myreid:myreid@127.0.0.1:5432/myreid}"
export SCENIC_DATABASE_URL="${SCENIC_DATABASE_URL:-postgresql+psycopg://myreid:myreid@127.0.0.1:5432/myreid_scenic}"
export SECRET_KEY="${SECRET_KEY:-local-dev-secret-change-before-production}"

"$ROOT_DIR/scripts/start_postgres.sh"

cd "$BACKEND_DIR"
exec "$VENV_PY" -m uvicorn main:app --host 127.0.0.1 --port 2889
