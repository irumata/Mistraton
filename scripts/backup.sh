#!/usr/bin/env bash
set -euo pipefail
umask 077
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${ENV_FILE:-$repo_dir/.env}"
if [[ -n "$(python3 "$repo_dir/scripts/env.py" "$env_file" DATABASE_URL)" ]]; then
  echo 'External database configured: replace backup.sh with an external pg_dump/offsite backup procedure before auto-deploy.' >&2
  exit 1
fi
backup_dir="${BACKUP_DIR:-$HOME/hackathon/shared/backups}"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
name="postgres-$(date -u +%Y%m%dT%H%M%SZ).dump"
partial="$backup_dir/$name.partial"
trap 'rm -f -- "$partial"' EXIT
bash "$repo_dir/scripts/compose.sh" exec -T db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner' > "$partial"
test -s "$partial"
mv "$partial" "$backup_dir/$name"
(cd "$backup_dir" && sha256sum "$name" > "$name.sha256")
find "$backup_dir" -maxdepth 1 -type f -name 'postgres-*.dump*' -mtime +7 -delete
echo "Backup saved: $backup_dir/$name (seven-day local retention)"
