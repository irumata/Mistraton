# Set up Mistraton on Oracle VM 130.162.187.46

These steps are account-specific and must be completed by someone with the VM's SSH key, Oracle Console access, a DuckDNS login, and repository administration rights. The VM's operating system, shape, capacity, current firewall, public-IP reservation, and existing workloads have not been inspected. Check them before changing the server. Keep an existing SSH session open during firewall or SSH changes.

## 1. Confirm the free allowance and VM

Use your **existing VM**. In Oracle Console, inspect its shape, memory, attached boot/block volumes, region, and current cost estimate. Do not create a new instance, reserve additional resources, or enlarge the shape until the console confirms the change remains free.

As checked on 20 November 2026, Oracle documents an Always Free A1 allowance of **1,500 OCPU-hours and 9,000 GB-hours per month**, equivalent to **2 OCPUs and 12 GB** for Always Free tenancies, with **200 GB combined boot/block storage** in the home region. Allowances depend on account terms and aggregate resources, not just this server. Oracle can reclaim idle Always Free instances. Keep an off-VM backup; free hosting is not an availability guarantee. [Oracle Always Free documentation](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).

This stack is a practical fit for a small A1 VM with several GB RAM. A 1 GB micro VM may struggle with builds and PostgreSQL; measure memory before deploying. Scaling containers does not create capacity. Do not upgrade the Oracle account or add paid capacity automatically. A budget alert is a notification, not a hard spending cap. Messenger providers and AI APIs can also have independent fees; this demo calls neither.

On your computer, clone the repository (authenticate to GitHub because it is private):

```bash
git clone https://github.com/irumata/Mistraton.git
cd Mistraton
```

Set these local shell variables, replacing the key path and selecting the existing VM's login. Oracle Linux normally uses `opc`; Ubuntu normally uses `ubuntu`.

```bash
ORACLE_KEY=/absolute/path/to/your/existing_oracle_private_key
ORACLE_LOGIN=opc
ssh -i "$ORACLE_KEY" "$ORACLE_LOGIN@130.162.187.46"
```

Verify the server's SSH fingerprint through a trusted existing connection or Oracle's console before accepting a first connection. On the VM, inspect:

```bash
cat /etc/os-release
uname -m
free -h
df -h
sudo ss -lntp
```

Ports 80/443 must be available. Do not stop an existing website without planning the change. `aarch64` means Arm64; `x86_64` means AMD64. Images build for the VM's native architecture.

## 2. Allocate your free DuckDNS subdomain

1. Sign in at [DuckDNS](https://www.duckdns.org/).
2. Choose an available name, for example `mistraton-demo`. The example is not reserved for you.
3. Create `YOURNAME.duckdns.org` and set its IPv4 address to **130.162.187.46**.
4. Leave IPv6 empty unless the VM has a working public IPv6 address and the matching firewall rules. A stale AAAA record can break HTTPS validation.
5. On your computer, confirm DNS:

```bash
dig +short A YOURNAME.duckdns.org
dig +short AAAA YOURNAME.duckdns.org
```

The A result must be `130.162.187.46`. Wait for propagation before deploying. An optional update helper is included:

```bash
python3 scripts/duckdns-update.py --subdomain YOURNAME --ip 130.162.187.46
```

It prompts for your DuckDNS token without printing it or storing it in GitHub. Run it only for a domain you own. Do not run the update with IP auto-detection from your laptop: that would point DNS at the laptop's network. No periodic updater is needed while this public IP remains assigned; after an IP change, update DNS, SSH variables and the pinned SSH host entry. Check the VM's public IP assignment in Oracle before assuming it survives a future replacement. [DuckDNS API](https://www.duckdns.org/spec.jsp).

## 3. Configure Oracle networking

In Oracle Console: **Compute → Instances → your VM → attached VNIC/subnet → applicable Network Security Group or Security List**. Ensure this is a public subnet with a route `0.0.0.0/0` to an Internet Gateway and a public IP on this VNIC.

Add **stateful ingress** rules, IP protocol TCP, source port range All:

| Destination port | Source CIDR | Purpose |
|---|---|---|
| 80 | `0.0.0.0/0` | HTTP redirect and ACME challenge |
| 443 | `0.0.0.0/0` | HTTPS |
| 22 | Your admin IP `/32`, plus deployment runner access | SSH |

Keep egress to DNS, HTTPS package registries / certificate authorities / external APIs available. The default stateful allow-all egress is sufficient. Do not add ingress for 5432, 6379, 8000 or 2015. Caddy's health listener 2015 is bound only to its own loopback.

**GitHub's standard hosted runners do not have one fixed outbound IP.** The simplest hackathon configuration permits TCP 22 from `0.0.0.0/0` and uses a dedicated key-only deploy account with a pinned server host key. Keeping 22 limited to your personal `/32` will prevent hosted Actions deployment. A stronger alternative is a private overlay or a separately maintained runner, but do not install a runner on this production VM to execute untrusted pull requests. A larger runner with a static IP may be paid; it is not used here. [GitHub runner networking](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

Also allow 80/443 in the VM's **existing host firewall**. Inspect it first:

```bash
sudo systemctl is-active firewalld || true
sudo ufw status 2>/dev/null || true
sudo iptables -S INPUT
```

For active **firewalld**, on the zone containing your public interface:

```bash
sudo firewall-cmd --get-active-zones
# Add --zone=YOUR_ACTIVE_ZONE below if it differs from the default.
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

For active **UFW**, preserve SSH before adjusting web rules:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status
```

Some Oracle Ubuntu images use raw iptables with a terminal REJECT rule instead. If that is your active firewall, add the web accept rule above the rejection:

```bash
sudo iptables -I INPUT 1 -p tcp -m multiport --dports 80,443 -j ACCEPT
# If netfilter-persistent manages this image's rules:
sudo netfilter-persistent save
```

If `netfilter-persistent` is absent, persist through the firewall manager your image actually uses; a plain iptables command alone does not survive reboot. Do not flush Oracle's firewall or remove its reserved-address rules. Docker-published ports can bypass UFW policies, so the Oracle rules and the Compose port list are the primary perimeter. Only web ports are published. [Docker firewall considerations](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations).

## 4. Install Docker and create a deploy account

The reviewed installer supports Ubuntu and Oracle Linux and installs Docker's official packages. It does not remove conflicting packages or change firewalls. For an existing Podman/Docker installation, follow the official conflict instructions before proceeding. [Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/), [Oracle Linux installation](https://docs.docker.com/engine/install/oraclelinux/).

From your computer, in the repository:

```bash
scp -i "$ORACLE_KEY" scripts/install-docker.sh "$ORACLE_LOGIN@130.162.187.46:/tmp/install-mistraton-docker.sh"
ssh -i "$ORACLE_KEY" "$ORACLE_LOGIN@130.162.187.46"
```

On the VM:

```bash
cat /tmp/install-mistraton-docker.sh
bash /tmp/install-mistraton-docker.sh
sudo docker compose version
sudo useradd --create-home --shell /bin/bash deploy
sudo usermod -aG docker deploy
sudo install -d -m 700 -o deploy -g deploy \
  /home/deploy/.ssh /home/deploy/hackathon \
  /home/deploy/hackathon/shared /home/deploy/hackathon/releases \
  /home/deploy/hackathon/incoming
```

If `deploy` already exists, inspect it and reuse it instead of repeating `useradd`. The Docker group grants effective root access. Only trusted maintainers should hold this deployment key or have repository write access.

## 5. Create the dedicated SSH deployment key

On your computer, generate a new key (use a different filename if one already exists):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/mistraton_deploy -N '' -C github-actions-mistraton
chmod 600 ~/.ssh/mistraton_deploy
sed 's/^/restrict /' ~/.ssh/mistraton_deploy.pub | \
  ssh -i "$ORACLE_KEY" "$ORACLE_LOGIN@130.162.187.46" \
  'sudo tee /home/deploy/.ssh/authorized_keys >/dev/null; sudo chown deploy:deploy /home/deploy/.ssh/authorized_keys; sudo chmod 600 /home/deploy/.ssh/authorized_keys'
ssh -i ~/.ssh/mistraton_deploy -o IdentitiesOnly=yes deploy@130.162.187.46 'docker compose version && docker ps'
```

This replaces the dedicated deploy account's `authorized_keys`. If you are reusing an account with existing keys, append the new restricted public key deliberately instead. `restrict` disables forwarding and PTY while allowing the non-interactive commands used here. It does not remove Docker's root-equivalent privilege.

Once your new key works, disable password/root SSH logins if not already disabled. On supported images with `sshd_config.d` included, create `/etc/ssh/sshd_config.d/00-mistraton.conf` containing:

```text
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
```

Validate with `sudo sshd -t` and inspect `sudo sshd -T` for the effective values. Reload `sshd` on Oracle Linux or `ssh` on Ubuntu. Keep your original session open and test a second login before closing it; an existing image configuration can take precedence.

## 6. Pin the VM's SSH host key

In the **trusted admin SSH session**, get the VM's own host public key, rather than trusting a new network scan:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
sudo awk '{print "130.162.187.46 " $1 " " $2}' /etc/ssh/ssh_host_ed25519_key.pub
```

Save the complete second output line as the GitHub secret `SSH_KNOWN_HOSTS`. It looks like `130.162.187.46 ssh-ed25519 AAAA...`. If you choose a non-default SSH port, its first field must instead be `[130.162.187.46]:PORT`. Compare the fingerprint through a trusted console if establishing access for the first time. The workflow uses `StrictHostKeyChecking=yes`; it never disables verification or runs an unverified scan.

## 7. Create the VM-only runtime secrets

From your computer, upload the generator and example to the deploy account:

```bash
scp -i ~/.ssh/mistraton_deploy scripts/configure.py deploy@130.162.187.46:hackathon/shared/configure.py
ssh -i ~/.ssh/mistraton_deploy deploy@130.162.187.46 \
  'python3 ~/hackathon/shared/configure.py --domain YOURNAME.duckdns.org --email YOU@example.com --output ~/hackathon/shared/.env'
```

Replace the domain and email first. `.env` gets mode 600 and independent random hex passwords; nothing is printed. The generator refuses to overwrite existing secrets. It sets one API and one worker initially. The VM keeps `.env` outside release directories; deployments link to it and never upload a developer `.env`.

Keep an encrypted copy in your own password manager. Do not commit `.env`, SSH private keys or DuckDNS tokens. Do not put secrets in Vite `VITE_*` variables: they become public JavaScript. Open `.env` through a trusted SSH session when you need the admin key for the demo. See [security limitations](SECURITY.md) before sharing it with anyone.

## 8. Configure GitHub settings

Repository: [irumata/Mistraton](https://github.com/irumata/Mistraton).

Because this repository is **private**, check account **Billing → budgets / spending controls → Actions** before enabling CI. Standard hosted runners are free for public repos; private repos consume the owner's allowance and can incur overage. Set the available Actions spending control to **stop usage at the included allowance / zero paid overage** and verify it is active. If your plan's controls do not provide a hard stop, leave hosted CI disabled and deploy manually until you have a no-charge option. A budget that only emails is insufficient. This setup does not change your billing settings. [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

In **Settings → Environments**, create `production`. If your plan exposes branch restrictions, allow `main` only. Leave required reviewers off if you want automatic deployments; enabling them intentionally makes each deployment wait for approval.

In **Settings → Secrets and variables → Actions → Secrets**, add:

| Secret | Value |
|---|---|
| `SSH_PRIVATE_KEY` | Entire contents of `~/.ssh/mistraton_deploy`, including BEGIN/END lines |
| `SSH_KNOWN_HOSTS` | Verified host key line from step 6 |

Add these **repository-level Actions variables** (not environment-only variables, because job conditions are evaluated before entering an environment):

| Variable | Value |
|---|---|
| `CI_ENABLED` | `true` only after the spending stop is verified |
| `DEPLOY_ENABLED` | `true` after steps 1–7 and secrets are ready |
| `SSH_HOST` | `130.162.187.46` |
| `SSH_USER` | `deploy` |
| `SSH_PORT` | `22` |
| `DEPLOY_DOMAIN` | `YOURNAME.duckdns.org`, no scheme or trailing slash |

CI uses only synthetic local secrets. Real database passwords, admin key, webhook secret, and DuckDNS token are never needed by the runner. Repository secrets are available to the production job; alternatively put SSH secrets in the `production` environment. Never use `pull_request_target` to execute contributions with production secrets. Restrict repository write access to trusted people. Protect `main` with checks/reviews if available on your plan.

## 9. First deployment and automatic updates

After variables and secrets are ready, open **Actions → Check and deploy → Run workflow**, selecting `main`. Later, every push to `main` checks and deploys automatically.

The workflow runs Python tests and linting, builds the frontend, builds Linux containers, starts two APIs and two workers, checks Caddy replica routing and a completed job, and tests database backup/restore. Deployment happens only after these pass. It uploads the exact commit; the VM never needs a GitHub access token. Builds happen natively on the Oracle VM, so the CI runner's AMD64 architecture is not imposed on an A1 Arm server.

The deployment creates `~/hackathon/releases/COMMIT_SHA`, builds tagged app images, backs up an existing database, applies migrations once, starts services, checks a completed job, then points `~/hackathon/current` at the successful release. Services share the fixed Compose project `hackathon`, so volumes survive path changes. Failed deployment attempts try to restore previous application images; database migrations are never automatically reversed. Normal Compose replacement can briefly interrupt traffic. This is not a zero-downtime or highly available system.

**Manual first deployment**, if hosted CI stays disabled (on your computer):

```bash
git pull --ff-only
MISTRATON_SHA=$(git rev-parse HEAD)
git archive --format=tar "$MISTRATON_SHA" | gzip > /tmp/mistraton-release.tgz
ssh -i ~/.ssh/mistraton_deploy deploy@130.162.187.46 \
  "mkdir -p ~/hackathon/releases/$MISTRATON_SHA"
scp -i ~/.ssh/mistraton_deploy /tmp/mistraton-release.tgz \
  deploy@130.162.187.46:hackathon/incoming/manual.tgz
ssh -i ~/.ssh/mistraton_deploy deploy@130.162.187.46 \
  "tar -xzf ~/hackathon/incoming/manual.tgz -C ~/hackathon/releases/$MISTRATON_SHA && bash ~/hackathon/releases/$MISTRATON_SHA/scripts/deploy.sh $MISTRATON_SHA"
```

Use verified SSH hosts locally too. This uploads committed files only, without `.env` or private credentials. Manually deploying bypasses hosted checks: run the local checks in [VALIDATION.md](VALIDATION.md) first.

## 10. Validate the live site

From your computer:

```bash
curl -I http://YOURNAME.duckdns.org
curl -I https://YOURNAME.duckdns.org
curl --fail https://YOURNAME.duckdns.org/api/ready
curl -i -X POST https://YOURNAME.duckdns.org/api/jobs \
  -H 'Content-Type: application/json' -d '{"text":"must be rejected"}'
```

Expect HTTP → HTTPS redirect, a valid trusted certificate, readiness JSON `{"status":"ready"}`, and `401` for the unauthenticated job. Open the website, enter your VM's `ADMIN_API_KEY`, submit a message, and verify the uppercase result appears with status `finished`.

On the VM, using the admin account with `sudo -iu deploy` if you want an interactive shell (the restricted deployment key disables PTY):

```bash
cd ~/hackathon/current
bash scripts/compose.sh ps
bash scripts/compose.sh logs --tail 80 api worker web
python3 scripts/smoke.py
```

Check that all expected workers are running, no DB/Redis ports are published, and that the site returns after a planned VM reboot. Caddy obtains/renews the certificate automatically when DNS and TCP 80/443 are correct. Its `/data` volume must remain persistent. [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).

If HTTPS fails, check A/AAAA DNS, both firewall layers, availability of 80/443, Caddy logs, and certificate authority rate limits. For `502`, check API health and Docker DNS. For jobs stuck `queued`, check worker logs and Redis credentials. For failure after changing database passwords, see [operations](OPERATIONS.md): changing `.env` does not change an existing database user's password.
