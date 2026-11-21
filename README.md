# Mistralethon — **SAFETRAL** · Le Chonk in London hackathon submission (21 Nov 2026)

**What it is:** an AI-powered security-monitoring demo. A live Mistral Large 4 agent works against a local GitHub-like service; the safetral pipeline watches for the classic MCP-style incident — a public issue that triggers a **private-repository read followed by a public publication** — detected by numerical forecast bands (Timestral panel) + deterministic boundary rules, then reviewed and escalated/dismissed by a separate Mistral Large 4 reviewer. Pixel-themed dashboard (Watch / Pipeline / Timestral / Upload panels) with live SSE event stream, recorded-run replay, and **voice alerts via Voxtral TTS** (`/api/voice/*`).

- **Live demo:** https://safestral.dpdns.org/ — HTTP Basic login: username `safestral`, password in the submission notes
- **Demo video:** see submission (video field) — recorded run + live alert + voice playback
- **Repo:** https://github.com/mistraton/Mistraton (this repo)
- **Quickstart:** `docker compose up --build` (see SETUP.md / OPERATIONS.md)

**Mistral tech used:** Mistral Large 4 (live agent + independent reviewer) · **Voxtral TTS** (`POST /api/voice/say`, Special_03) · Timestral forecast panel (`/sentinel/timestral/chat`) · Vibe-built (this branch was prepared with Vibe). Tracks: Track_01 (+ Special_03 if voice enabled at submit time — verify `GET /api/voice/status`).

**Notes for judges:** secrets scan is clean (`git grep` for keys/.env — empty). Recent Actions runs fail because the deploy workflow needs VM secrets; the app is live and healthy at the URL above. Demo plan & acceptance checks: `docs/SAFETRAL-DEMO-PLAN.md`.

---

# Mistralethon
my small change
A working React/Vite frontend, FastAPI API, PostgreSQL 17 database and Caddy HTTPS gateway for the Oracle Linux 9 **x86_64** VM at `130.162.187.46`.

| Item | Value |
| --- | --- |
| App title | Mistralethon |
| Mac folder | `/Users/rumata/Developing/Mistralethon` |
| VM folder | `/home/opc/Mistralethon` |
| GitHub repository | `irumata/mistraleton` by default |
| Domain | `mistraleton.duckdns.org` |
| SSH login | `opc@130.162.187.46` |

The connected GitHub account was verified as **irumata**. If the target is another account/organization, use that owner when bootstrapping and creating the repository. CI derives and lowercases the actual repository owner; the VM must match. Permission to create the repository, administer Actions secrets and publish packages is still required. No repository was published by this setup task; no accessible `irumata/mistraleton` repository was found.

## Included

- React page with live application/database checks and a retry button.
- `GET /api/health`: liveness, returns `{"status":"ok","project":"Mistralethon"}`.
- `GET /api/db`: checks the real application schema; HTTP 503 on database failure, without exposing connection details.
- `/api/docs` and `/api/openapi.json`: FastAPI documentation.
- Idempotent SQL migration, schema version 1, and separate administrator/application credentials. The API connects as **non-superuser** `app`; only the migration service receives the administrator credential.
- PostgreSQL `17-alpine`, persistent named database/Caddy/Redis volumes, Docker log rotation and memory limits. No host bind mounts, avoiding SELinux relabeling issues.
- Redis/RQ behind the **off-by-default** `jobs` profile, with an example job and no public enqueue endpoint.
- Actions builds/tests **linux/amd64** images, publishes to GHCR and deploys the tested commit by verified SSH. Nothing builds on the VM.

The steady default container limits total **480 MiB** (DB 192 + API 192 + web 96); migration temporarily allows another 128 MiB. Docker, the OS and caches require additional memory. The existing approximately 945 MiB swap helps with spikes; this is a small starter, not a capacity guarantee. Keep jobs off on the 945 MiB VM unless memory measurements justify enabling them.

Existing source was examined before changes. A source backup is in `.preexisting/original-source-before-20261121.tar.gz`; misplaced configuration files were moved to `.preexisting/frontend/src/`. `App-base.jsx` and unused original assets remain. `.preexisting/` is excluded from Git and Docker builds.

## 1. DuckDNS and Oracle Cloud ingress

In [DuckDNS](https://www.duckdns.org/), set **mistraleton** to **130.162.187.46**. No DuckDNS token is needed in the project. Prefer a reserved public IP; update DNS and SSH configuration together if it changes.

On the Mac:

```sh
dig +short A mistraleton.duckdns.org
dig +short AAAA mistraleton.duckdns.org
```

The A record must be `130.162.187.46`. Remove a stale AAAA unless it actually points to this VM's reachable IPv6 address with matching firewall rules. DNS resolved correctly during setup; recheck before deployment.

In Oracle Cloud, inspect the instance VNIC/subnet and attached **Security Lists/Network Security Groups**. Ensure a public IP, route to an Internet Gateway and **stateful TCP** ingress rules, with source port range **All**:

| Destination | Source | Purpose |
| --- | --- | --- |
| 22 | Your public IP `/32`, plus the deployment runner's source | SSH |
| 80 | `0.0.0.0/0` | Certificate challenge/HTTP redirect |
| 443 | `0.0.0.0/0` | HTTPS |

GitHub-hosted runners have changing source IPs. Allowing port 22 only from your Mac blocks Actions. Either allow TCP 22 from `0.0.0.0/0` with key-only SSH for this starter, or use a runner with fixed egress and restrict ingress to that address plus your Mac. Preserve existing SSH access.

Allow outbound DNS, HTTPS to registries/certificate authorities and stateful return traffic; the usual allow-all egress covers this. Do **not** expose 5432, 6379, 8000, 8081 or Docker's API. Compose publishes only TCP 80/443. HTTP/3 is disabled, so UDP 443 is unnecessary. OCI ingress is the outer firewall; Docker manages container NAT/forwarding.

## 2. Verify SSH before uploading

You need the existing Oracle **private SSH key** authorized for `opc`. This project cannot infer or replace that original credential. Keep it outside Git.

Get the VM's **ED25519 host-key fingerprint** through Oracle's serial console/console connection or another independently trusted access path:

```sh
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

Use the `SHA256:...` value from that trusted output. An unverified `ssh-keyscan` alone does not authenticate a server. A previously verified SSH connection is also acceptable; first-time unverified SSH would be circular verification.

On the Mac (works in zsh too):

```sh
cd /Users/rumata/Developing/Mistralethon
printf 'Trusted ED25519 SHA256 fingerprint from Oracle console: '
read -r VM_HOST_FINGERPRINT
bash scripts/verify-host.sh "$VM_HOST_FINGERPRINT"
printf 'Full path to your existing Oracle SSH private key: '
read -r SSH_IDENTITY_FILE
export SSH_IDENTITY_FILE
chmod 600 "$SSH_IDENTITY_FILE"
ssh -i "$SSH_IDENTITY_FILE" -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$PWD/.deploy/known_hosts" \
  -o ConnectTimeout=10 opc@130.162.187.46 'whoami; uname -m; sudo -n true'
```

Expected: `opc`, `x86_64`, no sudo password prompt. An encrypted original key may prompt for its passphrase; the Mac SSH agent is supported. If login fails, check the VM, your current public-IP ingress and the correct original key before continuing.

## 3. Bootstrap Oracle from the Mac

With `SSH_IDENTITY_FILE` still exported:

```sh
bash scripts/bootstrap-mac.sh irumata
```

This strictly verifies the host, uploads only `setup-vm.sh` to a random temporary directory, executes it as `opc` and removes that upload. It never copies the Mac `.env`.

The VM bootstrap:

1. Verifies Oracle Linux 9 x86_64 and passwordless sudo.
2. Uses a DNF allowlist: **ol9_baseos_latest + ol9_appstream**, adding **docker-ce-stable** for Docker. All others, including **ol9_oci_included and ol9_ksplice**, are bypassed on every DNF command. No full upgrade or all-repository metadata download.
3. Installs the official Oracle Linux Docker CE repository, verifies Docker's GPG key fingerprint and installs Engine/Compose with signature verification. It refuses to overwrite an existing repository for another distribution.
4. Enables firewalld; opens SSH/HTTP/HTTPS in the active/default interface zone, both runtime and permanently. Avoids a firewall reload that might erase Docker rules.
5. Enables Docker at boot; uses `sudo docker`, with no group/login refresh required.
6. Creates `/home/opc/Mistralethon/.env` mode 600, with **two independent random passwords**, inside a mode 700 directory. Preserves and validates existing configuration. Reports memory and disk; does not replace swap or change kernel settings.

Do not install Node, build images or run a local model on this 1 GB VM. If DNF is already stuck, inspect `ps -ef | grep '[d]nf'` and stop that specific stalled process normally. Do not delete a live package-manager lock or indiscriminately kill package processes.

### Existing legacy iptables-services or Podman compatibility

The bootstrap stops before changing an active legacy firewall. Do not run firewalld alongside `iptables.service`/`ip6tables.service` restoring rules over Docker. Do not disable SELinux, flush firewall rules or set Docker's `iptables=false`.

If migrating this starter VM, use the **Oracle console with recovery access open**, inspect other workloads/rules and install firewalld while the existing firewall still runs:

```sh
sudo dnf --disablerepo='*' --enablerepo=ol9_baseos_latest --enablerepo=ol9_appstream \
  --setopt=install_weak_deps=False install -y firewalld
sudo sh -c 'umask 077; iptables-save > /root/iptables.before-mistraleton.rules; ip6tables-save > /root/ip6tables.before-mistraleton.rules'
sudo firewall-offline-cmd --add-service=ssh
sudo firewall-offline-cmd --add-service=http
sudo firewall-offline-cmd --add-service=https
```

Carry any other required rules into the correct firewalld zone. Then, from the console:

```sh
sudo systemctl disable --now iptables.service
# Only if ip6tables.service is installed:
sudo systemctl disable --now ip6tables.service
sudo systemctl enable --now firewalld
sudo firewall-cmd --get-active-zones
```

Confirm a **new** verified SSH login works and rerun `bootstrap-mac.sh`. If Docker was already running during the firewall switch, restart it afterward (`sudo systemctl restart docker`) to recreate NAT/forwarding rules; this briefly interrupts existing containers. Keep rule backups until access is confirmed.

If `podman-docker` is detected, inspect existing Podman workloads. If that Docker-compatibility package is unused, remove only it using `sudo dnf --disablerepo='*' --enablerepo=ol9_baseos_latest --enablerepo=ol9_appstream remove podman-docker`, then retry. The script never erases Podman data or automatically removes conflicting software.

## 4. Create the dedicated Actions SSH key

On the Mac, with the original `SSH_IDENTITY_FILE` still exported:

```sh
bash scripts/setup-deploy-key-mac.sh
```

This creates `.deploy/id_ed25519` only if absent, adds its public key without duplicating it and tests passwordless sudo through that key. The `restrict` key option disables PTY/forwarding while allowing deployment SSH/SFTP commands. Keep the original Oracle key for recovery.

This key gives the workflow command execution as `opc`, which has sudo access. Trust people allowed to push to `main`; use branch protection if others join.

## 5. Create GitHub repository and secrets

You need permission to create **irumata/mistraleton**, administer its Actions secrets and publish packages under `irumata`. Organization policies may need to allow Actions/package creation. The connected account and local GitHub CLI were authenticated as `irumata` during setup.

On the Mac:

```sh
cd /Users/rumata/Developing/Mistralethon
gh auth status
# If needed: gh auth login
# This folder had no Git repository when inspected:
git init -b main
git add .
git status --short
```

Confirm `.env`, `.deploy/`, `.preexisting/`, `.venv/` and `node_modules/` are absent from staged files. They are already excluded. Then:

```sh
git commit -m "Create deployable Mistralethon application"
gh repo create irumata/mistraleton --private --source=. --remote=origin
gh secret set OCI_HOST --repo irumata/mistraleton --body '130.162.187.46'
gh secret set OCI_SSH_PRIVATE_KEY --repo irumata/mistraleton < .deploy/id_ed25519
gh secret set OCI_KNOWN_HOSTS --repo irumata/mistraleton < .deploy/known_hosts
```

If the repository already exists, inspect its content and use it instead; set `origin` to `https://github.com/irumata/mistraleton.git` only after reconciling existing work. Do not force-push. If owned elsewhere, change the owner consistently.

In **Settings → Actions → General**, permit the pinned GitHub-owned checkout/setup actions and workflow execution. The workflow explicitly requests `packages: write` to publish and `packages: read` to deploy. An organization policy can still deny those capabilities; an owner must allow them.

Do not create a `GITHUB_TOKEN` secret; GitHub supplies it. Never commit private keys, `.env`, DuckDNS tokens, PATs or database passwords. The `OCI_*` values go in **Actions secrets**, not source files.

### First GHCR deployment: private or public

The default workflow supports **private images on the first deploy**: it publishes using `GITHUB_TOKEN`, links images to this repository with an OCI source label and sends the deploy job's short-lived read token over verified SSH. The VM uses a temporary private Docker configuration under `/run` and deletes it afterward. No permanent GHCR PAT is needed on Oracle.

Images are `ghcr.io/irumata/mistraleton-api:<commit-sha>` and `ghcr.io/irumata/mistraleton-web:<commit-sha>`, plus `latest`. Deployments use and persist the SHA so manual restarts do not unexpectedly select `latest`.

If either package already exists from an unrelated/manual publish, open its package settings and grant this repository **Actions access** (write for build, read for deploy), or connect/inherit permissions appropriately. Public source repositories do not automatically make their packages public.

For anonymous manual pulls, explicitly make **both GHCR packages public** after publishing. Anyone can download public package contents. For private manual pulls outside Actions, use a **classic PAT** with `read:packages`, package access and SSO authorization if required. Enter it on the VM without adding it to shell history:

```sh
printf 'GitHub package-reader login: '
read -r GHCR_LOGIN
printf 'Classic PAT with read:packages: '
read -r -s GHCR_READ_TOKEN
printf '\n'
printf '%s' "$GHCR_READ_TOKEN" | sudo docker login ghcr.io -u "$GHCR_LOGIN" --password-stdin
unset GHCR_READ_TOKEN
```

Use `sudo docker login` for `sudo docker compose`; Mac/unprivileged logins do not authenticate root's VM Docker client. That optional manual login is stored in root's configuration (encoded, not encrypted); use a credential helper or `sudo docker logout ghcr.io` when finished. The supplied workflow uses ephemeral authentication instead.

## 6. Push and verify

After DNS, ingress, VM bootstrap and secrets are ready:

```sh
git push -u origin main
gh run list --repo irumata/mistraleton --limit 3
gh run watch --repo irumata/mistraleton
```

The workflow tests/builds first, publishes second, deploys third. Other branches do not deploy. To publish only, run Actions manually on `main` with `deploy` unchecked, or use `gh workflow run deploy.yml --repo irumata/mistraleton --ref main -f deploy=false`.

Deployment uploads only Compose/the deployment script to a commit-specific release directory, validates configuration, pulls serially to reduce memory pressure, waits for schema/API/web health and verifies the public HTTPS DB route. It never tears down the stack or deletes a data volume. Failed checks fail the job instead of reporting success.

From the Mac:

```sh
curl -fsS https://mistraleton.duckdns.org/api/health
curl -fsS https://mistraleton.duckdns.org/api/db
open https://mistraleton.duckdns.org
```

Expected DB result:

```json
{"status":"ok","database":"connected","project":"Mistralethon","schema_version":1}
```

Caddy issues/renews certificates automatically with correct A/AAAA records, reachable TCP 80/443, outbound HTTPS and persistent data. Do not bypass certificate verification with `curl -k` as a completion check.

## Local development and checks

With a **running Docker engine/Desktop** and Compose >=2.24.4:

```sh
cd /Users/rumata/Developing/Mistralethon
bash scripts/init-local.sh
# Apple Silicon default; on Intel/x86_64: export LOCAL_PLATFORM=linux/amd64
docker compose -f compose.yaml -f compose.dev.yaml up -d --build --wait
open http://localhost:8080
```

The Mac `.env` is independent of VM credentials. Initialization generates passwords securely only if absent. The dev override builds locally, uses HTTP and binds only `127.0.0.1:8080`, with separate `mistraleton-dev` volumes. No GHCR or real DNS is required. This Mac had the Docker CLI/standalone Compose but **no running engine**. If `docker compose` is unavailable with Homebrew, use `docker-compose`, or configure its CLI plugin according to Homebrew's instructions.

Hot-reload frontend: `npm --prefix frontend ci`, then `npm --prefix frontend run dev`. Vite proxies `/api` to `127.0.0.1:8000`; it reports unavailable services if no backend runs. Prefer the complete local Compose stack for a ready-to-run environment.

Checks without an engine:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
PYTHONPATH=backend .venv/bin/python -m pytest -q backend/tests
npm --prefix frontend run lint
npm --prefix frontend run build
bash scripts/init-local.sh
docker compose config --quiet
docker compose -f compose.yaml -f compose.dev.yaml config --quiet
# Optional installed tools:
shellcheck scripts/*.sh
SITE_ADDRESS=http://localhost:8080 caddy validate --config Caddyfile --adapter caddyfile
```

The live DB test is opt-in with `TEST_DATABASE=1` and `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD` for the **app** role. Setup additionally initialized a temporary native PostgreSQL 17 DB, ran migrations twice and passed all four tests. A native Caddy/FastAPI/PostgreSQL smoke test also verified the built frontend/assets, SPA fallback, API routing/docs and safe HTTP 503 after stopping the database. Actions includes Docker runtime and worker smoke tests; those could not run locally without an engine. Static/local checks do not prove OCI, GHCR or deployed HTTPS works.

## Operations on Oracle

Using verified SSH:

```sh
cd /home/opc/Mistralethon
sudo docker compose ps
sudo docker compose logs --tail 80 api migrate web
sudo docker stats --no-stream
free -m
df -h
```

Default services restart with Docker after reboot; the completed migration container stays exited normally. Each container's logs rotate at roughly 10 MiB total. Volumes persist across replacements. **Never delete data volumes with `compose down -v` or volume pruning.** Do not change PostgreSQL's major version against the same volume without an upgrade plan.

Back up before meaningful changes, on the VM:

```sh
umask 077
mkdir -p backups
sudo docker compose exec -T db pg_dump -U postgres -d mistralethon -Fc \
  > "backups/mistralethon-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

Copy backups off the VM over verified SSH. Persistence is not backup. Changing `DB_ADMIN_PASSWORD` in `.env` does not update an initialized PostgreSQL password. Do not regenerate credentials or erase a volume to fix authentication; rotate with SQL using the existing administrator login.

Deployment keeps `compose.previous.yaml` and `.env.previous`. After checking schema compatibility, use previous config and already-pulled images for application rollback. There is no automatic destructive DB rollback. Monitor `sudo docker system df`; retain old commit images needed for recovery. The workflow does not prune user data or other projects.

### Optional Redis/RQ

The extra container limits total 192 MiB; on this VM that may leave too little OS headroom. Check memory/swap/OOM events first. To opt in, set `COMPOSE_PROFILES=jobs` in the **VM** `.env`:

```sh
sudo docker compose --profile jobs up -d --wait
sudo docker compose exec -T worker python -c 'from redis import Redis; from rq import Queue; j = Queue(connection=Redis(host="redis")).enqueue("jobs.add", 20, 22); print(j.id)'
sudo docker compose logs --tail 40 worker
```

Redis is internal, uses append-only persistence and `noeviction` so jobs are not silently discarded. The example adds two numbers. To disable, set `COMPOSE_PROFILES=` and explicitly stop/remove the already-running optional containers (a disabled profile alone does not stop them):

```sh
sudo docker compose --profile jobs stop worker redis
sudo docker compose --profile jobs rm -f worker redis
```

## Troubleshooting

- **DNF stalls:** keep the allowlist; avoid all-repository `makecache` during installation. Check for a previous DNF process.
- **Docker repo mismatch:** inspect/back up `/etc/yum.repos.d/docker-ce.repo` and explicitly correct stale CentOS/RHEL config. Bootstrap will not replace it silently.
- **Docker networking after firewall changes:** inspect `systemctl status firewalld iptables docker`, `sudo iptables -S` and Docker logs. Avoid competing legacy rule restoration. Restart Docker after intentional firewall migration/reload; keep SELinux and Docker firewall management enabled.
- **GHCR unauthorized:** check lowercase VM `GH_OWNER`, repository/package linkage and Actions package access. Existing manually published packages often require an explicit grant.
- **SSH timeout:** check VM and port 22 source ingress separately for Mac/runner. **Permission denied:** correct original/deployment key. **Host-key mismatch:** investigate through Oracle console; never disable strict checking or blindly trust a new scan.
- **HTTPS fails:** check A/AAAA, public IP, Internet Gateway/route, ingress 80/443, firewalld and web logs. Local health does not prove public reachability. Repeated issuance attempts can meet CA rate limits.
- **DB 503:** inspect db/migrate logs, original credentials and schema. An older volume containing the previous guide's `app` DB/superuser is not silently compatible with this project's schema. Preserve/backup data before migrating an old installation.
- **OOM:** keep jobs off, one API worker, builds on GitHub; inspect kernel logs and increase VM memory or reduce load. Swap is slower than RAM.

## Sentinel-Loop: Timestral incident detection (live in `backend/`)

`backend/sentinel_loop/` implements the system from `system_create_prompt.md`:
Mistral Large extracts metrics from raw incident data, Shieldstral labels/verifies/guards
(PII filtering, window labels, alert verification), and a small self-hosted **Timestral**
(5M default) is the only detector and the only tuned model. Pipeline:
`ingest -> extract-metrics -> label-windows -> build-dataset -> train-detection -> evaluate -> serve`,
with a feedback loop into the labeling store. The watcher is mounted into the live API at
`/sentinel/watch` (see `backend/sentinel_loop/README.md` for the full walkthrough, CLI
entry points, tests, and `docker-compose.yml` for the watcher + labeling store).

## Official references checked

- [Docker Engine on Oracle Linux](https://docs.docker.com/engine/install/oraclelinux/) — repository/packages/GPG key.
- [Docker firewalls](https://docs.docker.com/engine/network/packet-filtering-firewalls/) — Docker/firewalld NAT integration.
- [GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) and [package access](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility) — private/public pulls and Actions permissions.
- [Caddy HTTPS](https://caddyserver.com/docs/automatic-https), [Compose profiles](https://docs.docker.com/compose/how-tos/profiles/), [DuckDNS specification](https://www.duckdns.org/spec.jsp).

## Run the whole website on Google Colab

`notebooks/colab_website.ipynb` starts the entire app in one notebook — React frontend + FastAPI backend + Sentinel-Loop pipeline UI (upload / ingest / extract-metrics / label / train / evaluate / finetune-timestral) — and exposes it via a cloudflared tunnel. Backend: `backend/colab_app.py` (adds `/sentinel/upload`, `/sentinel/pipeline/*`, `/sentinel/timestral/chat` and serves `frontend/dist` on top of the shared `/sentinel` router). See `docs/colab-website.md`.
