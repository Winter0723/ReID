#!/usr/bin/env bash
# Consistent offline snapshot of every Compose-managed named volume.
set -euo pipefail
umask 077
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
COMPOSE_ARGS=()
[[ -z "${MYREID_COMPOSE_ENV_FILE:-}" ]] || COMPOSE_ARGS+=(--env-file "$MYREID_COMPOSE_ENV_FILE")
if [[ -n "${MYREID_COMPOSE_FILES:-}" ]]; then
  IFS=';' read -r -a files <<< "$MYREID_COMPOSE_FILES"
  for file in "${files[@]}"; do COMPOSE_ARGS+=(-f "$file"); done
fi
compose() { docker compose "${COMPOSE_ARGS[@]}" "$@"; }
mode="${1:-help}"
[[ "$mode" == backup || "$mode" == restore ]] || {
  echo 'Usage: backup_restore.sh backup|restore SNAPSHOT_DIRECTORY'
  echo 'Stop ALL stack writers first (including remote S3 writers). See docs/BACKUP_RESTORE.md.'
  exit 0
}
[[ -n "${2:-}" ]] || { echo 'Snapshot directory is required' >&2; exit 1; }
[[ -z "$(compose ps --status running -q)" ]] || { echo 'Stop the entire Compose stack before snapshot/restore' >&2; exit 1; }
# This script never restores over existing data or deletes a volume.
if [[ "$mode" == backup ]]; then
  [[ ! -e "$2" ]] || { echo 'Use a new snapshot directory' >&2; exit 1; }
  mkdir -p "$2"
else
  [[ -d "$2" && -f "$2/SHA256SUMS" && -f "$2/volumes.tsv" ]] || { echo 'Incomplete snapshot' >&2; exit 1; }
fi
snapshot="$(cd "$2" && pwd)"
mapfile -t volumes < <(compose config --format json | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(k+"\t"+v["name"]) for k,v in sorted(d["volumes"].items()) if not v.get("external")]')
[[ "${#volumes[@]}" -gt 0 ]] || { echo 'No managed volumes found' >&2; exit 1; }
if [[ "$mode" == restore ]]; then
  (cd "$snapshot" && sha256sum -c SHA256SUMS)
  # Preflight ALL target volumes before writing any of them.
  for entry in "${volumes[@]}"; do
    IFS=$'\t' read -r key volume <<< "$entry"
    [[ "$key" =~ ^[a-zA-Z0-9_-]+$ && -f "$snapshot/$key.tar" ]] || exit 1
    if docker volume inspect "$volume" >/dev/null 2>&1; then
      docker run --rm --mount "type=volume,src=$volume,dst=/data,readonly" alpine:3.21.3 sh -ec 'test -z "$(ls -A /data)"' || {
        echo "Refusing non-empty volume: $volume" >&2; exit 1;
      }
    fi
  done
fi
for entry in "${volumes[@]}"; do
  IFS=$'\t' read -r key volume <<< "$entry"
  [[ "$key" =~ ^[a-zA-Z0-9_-]+$ ]] || exit 1
  if [[ "$mode" == backup ]]; then
    docker volume inspect "$volume" >/dev/null
    docker run --rm --mount "type=volume,src=$volume,dst=/data,readonly" --mount "type=bind,src=$snapshot,dst=/backup" alpine:3.21.3 tar -cpf "/backup/$key.tar" -C /data .
    printf '%s\t%s\n' "$key" "$volume" >> "$snapshot/volumes.tsv"
  else
    docker volume create "$volume" >/dev/null
    docker run --rm --mount "type=volume,src=$volume,dst=/data" --mount "type=bind,src=$snapshot,dst=/backup,readonly" alpine:3.21.3 tar -xpf "/backup/$key.tar" -C /data
  fi
done
if [[ "$mode" == backup ]]; then
  (cd "$snapshot" && sha256sum ./*.tar volumes.tsv > SHA256SUMS)
fi
echo "$mode completed for managed volumes. External S3 requires object_store_backup.py; see docs/BACKUP_RESTORE.md."
