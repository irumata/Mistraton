#!/usr/bin/env bash
# Explicitly destructive; use only with a verified backup and an agreed maintenance window.
set -euo pipefail
[[ "${1:-}" == --confirm-replace && -f "${2:-}" ]] || { echo 'Usage: bash scripts/restore.sh --confirm-replace /absolute/path/postgres.dump' >&2; exit 1; }
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${ENV_FILE:-$repo_dir/.env}"
for setting in DATABASE_URL REDIS_URL; do
  [[ -z "$(python3 "$repo_dir/scripts/env.py" "$env_file" "$setting")" ]] || { echo 'External data services: use their restore procedures instead.' >&2; exit 1; }
done
dc=(bash "$repo_dir/scripts/compose.sh")
"${dc[@]}" run --rm --no-deps db pg_restore --list < "$2" > /dev/null
"${dc[@]}" stop web api worker
"${dc[@]}" up -d --wait db redis
# Start from an empty database: --clean alone leaves tables from newer migrations behind.
"${dc[@]}" exec -T db sh -c 'dropdb --force --if-exists -U "$POSTGRES_USER" -- "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" -- "$POSTGRES_DB"'
"${dc[@]}" exec -T db sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --single-transaction' < "$2"
# This stack owns Redis database 0. Never point this at a shared Redis database.
"${dc[@]}" exec -T redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli FLUSHDB' > /dev/null
bash "$repo_dir/scripts/up.sh"
echo 'Database restored, Redis queue reset. Review non-finished rows before requeueing any work.'
