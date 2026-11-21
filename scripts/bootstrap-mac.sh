#!/usr/bin/env bash
# Run ON THE MAC. Only the VM bootstrap script is uploaded, never local .env.
set -euo pipefail
cd "$(dirname "$0")/.."
owner=$(printf '%s' "${1:-irumata}" | tr '[:upper:]' '[:lower:]')
[[ "$owner" =~ ^[a-z0-9][a-z0-9-]{0,38}$ ]] || { echo 'Invalid GitHub owner.' >&2; exit 1; }
known_hosts=${SSH_KNOWN_HOSTS_FILE:-$PWD/.deploy/known_hosts}
[[ -s "$known_hosts" ]] || { echo 'Verify the VM host key first; see README step 2.' >&2; exit 1; }
ssh_options=(-o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" -o ConnectTimeout=10 -o ConnectionAttempts=1 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
if [[ -n ${SSH_IDENTITY_FILE:-} ]]; then
  [[ -f "$SSH_IDENTITY_FILE" ]] || { echo 'SSH_IDENTITY_FILE does not exist.' >&2; exit 1; }
  ssh_options+=(-i "$SSH_IDENTITY_FILE" -o IdentitiesOnly=yes)
fi
remote_dir=$(ssh "${ssh_options[@]}" opc@130.162.187.46 'umask 077; mktemp -d /home/opc/.mistraleton-bootstrap.XXXXXX')
[[ "$remote_dir" =~ ^/home/opc/\.mistraleton-bootstrap\.[A-Za-z0-9]+$ ]] || { echo 'Unexpected remote temporary path.' >&2; exit 1; }
trap 'ssh "${ssh_options[@]}" opc@130.162.187.46 "rm -rf -- '\''$remote_dir'\''" </dev/null || true' EXIT
scp "${ssh_options[@]}" scripts/setup-vm.sh "opc@130.162.187.46:$remote_dir/setup-vm.sh"
# Both values are validated above; no user-provided text is interpreted remotely.
# shellcheck disable=SC2029 # Validated local values intentionally expand here.
ssh "${ssh_options[@]}" opc@130.162.187.46 "bash '$remote_dir/setup-vm.sh' '$owner'"
