#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${ENV_FILE:-$repo_dir/.env}"
if [[ ! -f "$env_file" ]]; then
  echo "Missing .env. Run scripts/configure.py first." >&2
  exit 1
fi
if [[ -f "$repo_dir/.release-id" ]]; then
  RELEASE_ID="$(cat "$repo_dir/.release-id")"
  [[ "$RELEASE_ID" =~ ^[a-f0-9]{40}$ ]] || { echo 'Invalid release ID' >&2; exit 1; }
  export RELEASE_ID
fi
exec docker compose --project-name hackathon --project-directory "$repo_dir" --env-file "$env_file" -f "$repo_dir/compose.yaml" "$@"
