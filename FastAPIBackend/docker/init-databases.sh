#!/usr/bin/env bash
set -Eeuo pipefail

scenic_database="${SCENIC_POSTGRES_DB:-myreid_scenic}"

psql_args=(
  --username "$POSTGRES_USER"
  --dbname "$POSTGRES_DB"
)
if [[ -n "${POSTGRES_HOST:-}" ]]; then
  psql_args+=(--host "$POSTGRES_HOST")
fi

psql -v ON_ERROR_STOP=1 "${psql_args[@]}" \
  --set=scenic_database="$scenic_database" <<-'EOSQL'
SELECT format('CREATE DATABASE %I', :'scenic_database')
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = :'scenic_database'
)\gexec
EOSQL
