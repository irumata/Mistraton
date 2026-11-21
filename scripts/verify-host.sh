#!/usr/bin/env bash
# Compare with a fingerprint obtained through Oracle's console/trusted access.
set -euo pipefail
cd "$(dirname "$0")/.."
expected=${1:?Usage: bash scripts/verify-host.sh SHA256:trusted-fingerprint}
[[ "$expected" =~ ^SHA256:[A-Za-z0-9+/]{43}$ ]] || { echo 'Expected an OpenSSH SHA256 fingerprint.' >&2; exit 1; }
umask 077
mkdir -p .deploy
scan=$(mktemp .deploy/host-scan.XXXXXX)
trap 'rm -f "$scan"' EXIT
ssh-keyscan -T 10 -t ed25519 130.162.187.46 > "$scan" 2>/dev/null
[[ $(wc -l < "$scan" | tr -d ' ') == 1 ]] || { echo 'Expected exactly one ED25519 host key.' >&2; exit 1; }
actual=$(ssh-keygen -lf "$scan" -E sha256 | awk '{print $2}')
[[ "$actual" == "$expected" ]] || { printf 'Host key mismatch. Scanned fingerprint: %s\n' "$actual" >&2; exit 1; }
install -m 600 "$scan" .deploy/known_hosts
printf 'Verified %s. Saved .deploy/known_hosts for Mac and GitHub Actions.\n' "$actual"
