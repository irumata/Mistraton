#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dc=(bash "$repo_dir/scripts/compose.sh")
env_file="${ENV_FILE:-$repo_dir/.env}"
api_replicas="$(python3 "$repo_dir/scripts/env.py" "$env_file" API_REPLICAS 1)"
worker_replicas="$(python3 "$repo_dir/scripts/env.py" "$env_file" WORKER_REPLICAS 1)"
[[ "$api_replicas" =~ ^[1-9][0-9]*$ && "$worker_replicas" =~ ^[1-9][0-9]*$ ]] || { echo 'Replica counts must be positive integers' >&2; exit 1; }
"${dc[@]}" config --quiet
if [[ "${SKIP_BUILD:-0}" != 1 ]]; then "${dc[@]}" build api web; fi
"${dc[@]}" up -d --wait --wait-timeout 180 db redis
"${dc[@]}" run --rm migrate
"${dc[@]}" up -d --wait --wait-timeout 180 --scale api="$api_replicas" --scale worker="$worker_replicas" api worker web
"${dc[@]}" exec -T api python -m app.check
echo 'Services ready; end-to-end background job passed.'
