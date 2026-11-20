#!/usr/bin/env bash
# Runs on the VM; the workflow uploads a release first. No server-side GitHub credentials.
set -euo pipefail
umask 077
release_id="${1:-}"
[[ "$release_id" =~ ^[a-f0-9]{40}$ ]] || { echo 'Expected a full 40-character commit SHA' >&2; exit 1; }
deploy_root="${HACKATHON_ROOT:-$HOME/hackathon}"
release_dir="$deploy_root/releases/$release_id"
[[ -f "$deploy_root/shared/.env" && -f "$release_dir/compose.yaml" ]] || { echo 'Upload release and create shared/.env first' >&2; exit 1; }
exec 9>"$deploy_root/shared/deploy.lock"
flock -w 1800 9 || { echo 'Another deployment holds the lock' >&2; exit 1; }
printf '%s\n' "$release_id" > "$release_dir/.release-id"
ln -sfn "$deploy_root/shared/.env" "$release_dir/.env"
previous_dir=""
if [[ -L "$deploy_root/current" ]]; then previous_dir="$(readlink -f "$deploy_root/current")"; fi
dc=(bash "$release_dir/scripts/compose.sh")
"${dc[@]}" config --quiet
"${dc[@]}" build api web
if [[ -n "$previous_dir" ]]; then bash "$previous_dir/scripts/backup.sh"; fi
if ! SKIP_BUILD=1 bash "$release_dir/scripts/up.sh"; then
  echo 'Deployment failed. Attempting previous app images. Database migrations are NOT reversed.' >&2
  if [[ -n "$previous_dir" ]]; then
    bash "$previous_dir/scripts/compose.sh" up -d --wait --wait-timeout 180 api worker web || true
  fi
  exit 1
fi
ln -sfn "$release_dir" "$deploy_root/current.new"
mv -Tf "$deploy_root/current.new" "$deploy_root/current"
echo "Deployed $release_id"
