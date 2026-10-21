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
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export SECRET_KEY="${SECRET_KEY:-local-dev-secret-change-before-production}"
export MODEL_DEVICE="${WORKER_MODEL_DEVICE:-cpu}"
export RQ_QUEUE_NAME="${RQ_QUEUE_NAME:-media}"

"$ROOT_DIR/scripts/start_postgres.sh"

cd "$BACKEND_DIR"
exec "$VENV_PY" -m rq worker "$RQ_QUEUE_NAME" --url "$REDIS_URL"
