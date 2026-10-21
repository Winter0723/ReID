#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker/OrbStack is required for the local PostgreSQL service." >&2
  exit 1
fi

cd "$ROOT_DIR"
docker compose -f docker-compose.postgres.yml up -d postgres redis
FastAPIBackend/venv/bin/python scripts/check_postgres.py
