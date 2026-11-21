#!/usr/bin/env bash
# Invoked from an uploaded release by Actions. Optional registry token via stdin.
set -euo pipefail
umask 077
owner=${1:?GitHub owner required}
tag=${2:?Commit SHA required}
registry_user=${3:-}
[[ "$owner" =~ ^[a-z0-9][a-z0-9-]{0,38}$ && "$tag" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid owner or commit SHA.' >&2; exit 1; }
app_dir=/home/opc/Mistralethon
release_dir=$(cd "$(dirname "$0")/.." && pwd)
[[ "$release_dir" == "$app_dir/.releases/$tag" ]] || { echo 'Unexpected release path.' >&2; exit 1; }
[[ -f "$app_dir/.env" ]] || { echo 'Run the VM bootstrap before deploying.' >&2; exit 1; }
cd "$app_dir"
exec 9> .deploy.lock
flock -w 300 9
configured_owner=$(sed -n 's/^GH_OWNER=//p' .env)
[[ "$configured_owner" == "$owner" ]] || { echo 'GH_OWNER on VM does not match the publishing owner.' >&2; exit 1; }
auth_dir=$(sudo mktemp -d /run/mistraleton-ghcr.XXXXXX)
trap 'sudo rm -rf -- "$auth_dir"' EXIT
if [[ -n "$registry_user" ]]; then
  IFS= read -r registry_token
  [[ -n "$registry_token" ]] || { echo 'Missing short-lived GHCR token on stdin.' >&2; exit 1; }
  printf '%s' "$registry_token" | sudo env DOCKER_CONFIG="$auth_dir" docker login ghcr.io --username "$registry_user" --password-stdin
  unset registry_token
fi
compose=(sudo env "DOCKER_CONFIG=$auth_dir" COMPOSE_PARALLEL_LIMIT=1 "GH_OWNER=$owner" "IMAGE_TAG=$tag" docker compose --project-name mistraleton --env-file "$app_dir/.env" --project-directory "$app_dir" -f "$release_dir/compose.yaml")
"${compose[@]}" config --quiet
# Pull before changing running services; don't build on this 945 MiB VM.
"${compose[@]}" pull --policy always
if [[ -f compose.yaml ]]; then cp -p compose.yaml compose.previous.yaml; fi
if [[ -f .env ]]; then cp -p .env .env.previous; fi
install -m 600 "$release_dir/compose.yaml" compose.yaml
"${compose[@]}" up -d --no-build --wait --wait-timeout 240
# Persist the deployed SHA so future manual compose commands use the same images.
python3 - "$app_dir/.env" "$tag" <<'PY'
import os, sys, tempfile
path, tag = sys.argv[1:]
lines = [line for line in open(path).read().splitlines() if not line.startswith('IMAGE_TAG=')]
fd, tmp = tempfile.mkstemp(prefix='.env.', dir=os.path.dirname(path))
with os.fdopen(fd, 'w') as out:
    out.write('\n'.join(lines) + '\nIMAGE_TAG=' + tag + '\n')
os.replace(tmp, path)
PY
"${compose[@]}" ps
# Verify certificate issuance and real routing from outside the Docker network.
curl --fail --silent --show-error --retry 18 --retry-delay 5 --retry-all-errors --connect-timeout 5 --max-time 10 https://mistraleton.duckdns.org/api/db
printf '\nDeployed Mistralethon commit %s\n' "$tag"
