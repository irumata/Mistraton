#!/usr/bin/env bash
# Install a dedicated Actions key using your ORIGINAL Oracle login key.
set -euo pipefail
cd "$(dirname "$0")/.."
known_hosts=${SSH_KNOWN_HOSTS_FILE:-$PWD/.deploy/known_hosts}
[[ -s "$known_hosts" ]] || { echo 'Verify the host key first; see README.' >&2; exit 1; }
umask 077
mkdir -p .deploy
if [[ ! -e .deploy/id_ed25519 ]]; then
  [[ ! -e .deploy/id_ed25519.pub ]] || { echo 'A public key exists without its private key; review .deploy/ first.' >&2; exit 1; }
  ssh-keygen -q -t ed25519 -f .deploy/id_ed25519 -C mistraleton-github-actions -N ''
fi
if [[ ! -e .deploy/id_ed25519.pub ]]; then
  ssh-keygen -y -f .deploy/id_ed25519 > .deploy/id_ed25519.pub
fi
options=(-o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
if [[ -n ${SSH_IDENTITY_FILE:-} ]]; then options+=(-i "$SSH_IDENTITY_FILE" -o IdentitiesOnly=yes); fi
# stdin contains a PUBLIC key. restrict disables PTY/agent/port forwarding;
# shell commands/SFTP remain available for deployment.
ssh "${options[@]}" opc@130.162.187.46 'umask 077; mkdir -p ~/.ssh; chmod 700 ~/.ssh; key=$(cat); touch ~/.ssh/authorized_keys; if ! grep -Fq -- "$key" ~/.ssh/authorized_keys; then printf "\nrestrict %s\n" "$key" >> ~/.ssh/authorized_keys; fi; chmod 600 ~/.ssh/authorized_keys' < .deploy/id_ed25519.pub
ssh -i .deploy/id_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" -o ConnectTimeout=10 opc@130.162.187.46 'sudo -n true && echo "Deployment SSH ready"'
echo 'Private key kept in .deploy/id_ed25519. Upload through gh secret set, never git.'
