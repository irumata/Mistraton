#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -e .env ]]; then
  echo '.env already exists; leaving it unchanged.'
  exit 0
fi
umask 077
config_tmp=$(mktemp .env.XXXXXX)
trap 'rm -f "$config_tmp"' EXIT
cat > "$config_tmp" <<EOF
GH_OWNER=irumata
SITE_ADDRESS=mistraleton.duckdns.org
DB_ADMIN_PASSWORD=$(openssl rand -hex 24)
DB_PASSWORD=$(openssl rand -hex 24)
IMAGE_TAG=latest
COMPOSE_PROFILES=
EOF
# noclobber refuses a concurrent initialization instead of replacing credentials.
(set -o noclobber; cat "$config_tmp" > .env)
echo 'Created private local .env with random database credentials.'
