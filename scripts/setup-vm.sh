#!/usr/bin/env bash
# Run as opc on the specified Oracle Linux 9 x86_64 VM.
set -euo pipefail
[[ $(id -un) == opc && $EUID -ne 0 ]] || { echo 'Run as opc, with passwordless sudo.' >&2; exit 1; }
# shellcheck disable=SC1091
source /etc/os-release
[[ "$ID" == ol && ${VERSION_ID%%.*} == 9 && $(uname -m) == x86_64 ]] || { echo 'Expected Oracle Linux 9 x86_64.' >&2; exit 1; }
owner=$(printf '%s' "${1:-irumata}" | tr '[:upper:]' '[:lower:]')
[[ "$owner" =~ ^[a-z0-9][a-z0-9-]{0,38}$ ]] || { echo 'Invalid GitHub owner.' >&2; exit 1; }
sudo -n true
app_dir=/home/opc/Mistralethon
# Do not tear down a legacy firewall or remove Podman packages automatically.
if systemctl is-active --quiet iptables || systemctl is-active --quiet ip6tables; then
  echo 'Legacy iptables-services is active. Follow README firewall migration, then rerun.' >&2
  exit 1
fi
if rpm -q podman-docker >/dev/null 2>&1; then
  echo 'podman-docker conflicts with Docker CE. Review/remove that compatibility package first.' >&2
  exit 1
fi
# A strict allowlist avoids ol9_oci_included, ol9_ksplice AND other huge metadata.
# These are Oracle Linux repos, not RHEL subscription repos.
DNF=(sudo dnf --disablerepo='*' --enablerepo=ol9_baseos_latest --enablerepo=ol9_appstream --setopt=install_weak_deps=False --setopt=timeout=30 --setopt=retries=2)
"${DNF[@]}" install -y dnf-plugins-core curl openssl gnupg2 python3 firewalld
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
mkdir -m 700 "$scratch/gnupg"
curl --proto '=https' --tlsv1.2 -fsSL --connect-timeout 15 --max-time 90 https://download.docker.com/linux/oracle/gpg -o "$scratch/docker.gpg"
fingerprint=$(gpg --batch --no-options --homedir "$scratch/gnupg" --show-keys --with-colons "$scratch/docker.gpg" 2>/dev/null | awk -F: '$1 == "fpr" {print $10; exit}')
[[ "$fingerprint" == 060A61C51B558A7F742B77AAC52FEB6B621E9F35 ]] || { echo 'Docker signing key fingerprint mismatch.' >&2; exit 1; }
sudo rpm --import "$scratch/docker.gpg"
# Use the currently documented, official Oracle Linux Docker CE repository.
# No curl-to-shell installer; repository packages retain GPG verification.
if [[ -e /etc/yum.repos.d/docker-ce.repo ]]; then
  if ! grep -q 'https://download.docker.com/linux/oracle/' /etc/yum.repos.d/docker-ce.repo; then
    echo 'Existing Docker repo is not the Oracle repo. Inspect it before replacing it.' >&2
    exit 1
  fi
else
  "${DNF[@]}" config-manager --add-repo https://download.docker.com/linux/oracle/docker-ce.repo
fi
# Repeat the allowlist on EVERY package-manager invocation, including this one.
"${DNF[@]}" --enablerepo=docker-ce-stable --setopt=docker-ce-stable.gpgcheck=1 install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# Configure the host firewall before starting Docker. Never flush iptables or
# set Docker's iptables=false: that would break container NAT/isolation.
sudo systemctl enable --now firewalld
interface=$(ip -4 route show default | awk 'NR == 1 {print $5}')
zone=$(sudo firewall-cmd --get-zone-of-interface="$interface" || true)
if [[ -z "$zone" || "$zone" == 'no zone' ]]; then zone=$(sudo firewall-cmd --get-default-zone); fi
for service in ssh http https; do
  sudo firewall-cmd --zone="$zone" --add-service="$service"
  sudo firewall-cmd --permanent --zone="$zone" --add-service="$service"
done
# Runtime + permanent changes above avoid reloading/erasing Docker's rules.
sudo systemctl enable --now docker
sudo docker info >/dev/null
sudo docker compose version
umask 077
mkdir -p "$app_dir"
chmod 700 "$app_dir"
if [[ ! -e "$app_dir/.env" ]]; then
  config_tmp=$(mktemp "$app_dir/.env.XXXXXX")
  cat > "$config_tmp" <<EOF
GH_OWNER=$owner
SITE_ADDRESS=mistraleton.duckdns.org
DB_ADMIN_PASSWORD=$(openssl rand -hex 24)
DB_PASSWORD=$(openssl rand -hex 24)
IMAGE_TAG=latest
COMPOSE_PROFILES=
EOF
  (set -o noclobber; cat "$config_tmp" > "$app_dir/.env")
  rm -f "$config_tmp"
else
  echo 'Preserving existing .env and credentials.'
fi
# Read data as data. Never source a deployment .env as executable shell code.
python3 - "$app_dir/.env" "$owner" <<'PY'
import os, re, sys
path, expected = sys.argv[1:]
values = {}
for line in open(path):
    if line.strip() and not line.lstrip().startswith('#'):
        key, sep, value = line.rstrip('\n').partition('=')
        if not sep or key in values:
            raise SystemExit('Invalid or duplicate .env assignment; review .env on the VM.')
        values[key] = value
if values.get('GH_OWNER') != expected:
    raise SystemExit('Existing .env GH_OWNER differs; review it before deployment.')
for key in ('DB_ADMIN_PASSWORD', 'DB_PASSWORD'):
    if not re.fullmatch(r'[0-9a-f]{48}', values.get(key, '')):
        raise SystemExit('Existing .env has missing/old credentials. See README; do not reset a live database volume.')
os.chmod(path, 0o600)
PY
free -m
df -h / /var/lib/docker
printf '\nVM bootstrap finished. Credentials remain only in %s/.env.\nNext: add the dedicated deployment SSH key and GitHub Actions secrets, then push main.\n' "$app_dir"
