#!/usr/bin/env bash
# Run on the Oracle VM after reviewing this file. Does not remove existing runtimes or alter firewalls.
set -euo pipefail
source /etc/os-release
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  echo 'Docker + Compose already installed.'
  exit 0
fi
case "$ID" in
  ubuntu)
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl git python3 rsync util-linux
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    distro_arch="$(dpkg --print-architecture)"
    printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$VERSION_CODENAME" "$distro_arch" | sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ;;
  ol)
    sudo dnf install -y dnf-plugins-core git python3 rsync util-linux
    sudo dnf config-manager --add-repo https://download.docker.com/linux/oracle/docker-ce.repo
    sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ;;
  *) echo "Unsupported OS: $ID. Follow Docker's official instructions; Ubuntu or Oracle Linux supported here." >&2; exit 1 ;;
esac
sudo systemctl enable --now docker
echo 'Docker installed. Add only your trusted deploy account to the docker group, then reconnect.'
