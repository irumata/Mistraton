# Mistraton

A simple static website on **Cloudflare Pages** with a Python API on an
**Oracle Cloud VM**. After the one-time setup below, pushing to `main` updates
both automatically. No npm packages or Python packages are needed.

## Run locally

Use Python 3.10+ and run these commands from the repository root in separate terminals:

```sh
python3 backend/server.py
python3 -m http.server 8080 --bind 127.0.0.1 --directory frontend
```

Open http://localhost:8080. The page should show “Hello from your Oracle Cloud VM!”
(the API runs locally for this check). `frontend/config.js` defaults to the local
API; after configuring production, change it back locally without committing, or
run the frontend against your production API with its matching allowed origin.

Tests and JavaScript syntax checks:

```sh
python3 -m unittest discover -s backend -v
node --check frontend/config.js
node --check frontend/app.js
```

## 1. Prepare the Oracle VM

These instructions target an Ubuntu 24.04 VM with systemd. Use a reserved public
IPv4 address and point an `A` record such as `api.example.com` at it. If using
Cloudflare DNS, start with **DNS only** for this API record.

1. Install Python 3 and curl (`sudo apt update && sudo apt install -y python3 curl`).
   Install [Caddy using its official Ubuntu instructions](https://caddyserver.com/docs/install#debian-ubuntu-raspbian).
2. Create a dedicated, unprivileged user: `sudo adduser deploy`. Keep the VM's
   administrator account separate; deployments need no sudo access.
3. Generate a dedicated deployment SSH key on your own machine with
   `ssh-keygen -t ed25519 -f ~/.ssh/mistraton_deploy`. Use an empty passphrase
   for this automation-only key. Add its **public** key to
   `/home/deploy/.ssh/authorized_keys` on the VM. Set `.ssh` to mode `700`,
   `authorized_keys` to `600`, both owned by `deploy`. Never commit the private key.
4. As administrator, run `sudo loginctl enable-linger deploy` so the user service
   starts on boot and survives logout.
5. Log in over SSH **as deploy** and run:

   ```sh
   mkdir -p ~/mistraton/backend ~/.config/systemd/user
   ```

   Copy `backend/server.py` to `~/mistraton/backend/server.py` and
   `deploy/mistraton.service` to `~/.config/systemd/user/mistraton.service`
   using `scp` from your checkout. Create `~/.config/mistraton.env`:

   ```ini
   ALLOWED_ORIGIN=https://YOUR-PROJECT.pages.dev
   PORT=8000
   ```

   Use your exact frontend origin, with no trailing slash. This file stays on
   the VM, outside the deployed code. Then run as `deploy`:

   ```sh
   chmod 600 ~/.config/mistraton.env
   export XDG_RUNTIME_DIR="/run/user/$(id -u)"
   systemctl --user daemon-reload
   systemctl --user enable --now mistraton
   curl --fail http://127.0.0.1:8000/api/health
   ```

6. As administrator, copy `deploy/Caddyfile` to `/etc/caddy/Caddyfile`, replacing
   `api.example.com` with your API hostname. Validate and reload:

   ```sh
   sudo caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   ```

   Allow inbound TCP **80 and 443** in both the Oracle VCN security list/network
   security group and the VM firewall. Allow TCP **22** for SSH deployment
   (GitHub-hosted runners need connectivity; for restricted networks use a
   suitably secured runner with access). Do **not** expose port 8000: the API
   binds only to loopback. Caddy obtains and renews HTTPS certificates.

Verify `https://api.example.com/api/health` returns `{"status": "ok"}`.

## 2. Connect Cloudflare Pages

1. In Cloudflare, choose **Workers & Pages → Create → Pages → Connect to Git**,
   authorize GitHub, and select this repository. Choose a **Pages Git integration**
   project, not a Worker or Direct Upload project.
2. Set production branch to **main**, framework preset to **None**, leave the
   build command **empty**, and set build output directory to **frontend**.
   Leave the root directory at the repository root. No build variables or
   Cloudflare API tokens are required.
3. Set `window.API_BASE_URL` in `frontend/config.js` to your public **HTTPS**
   backend URL (for example `https://api.example.com`) and commit it.
   This is public browser configuration; never put secrets here.
4. Set the VM's `ALLOWED_ORIGIN` to the actual Pages production origin, then run
   `systemctl --user restart mistraton` as `deploy`. If you add a custom frontend
   domain, use that origin instead. Preview URLs are not granted API CORS access
   by default. CORS is not authentication; these demo endpoints are public.

Cloudflare now publishes `frontend/` automatically on every push to `main`.
Cloudflare deployments and the backend workflow run independently, so keep API
changes backward-compatible. Pages preview deployments do not deploy the VM.

## 3. Enable automatic backend deployments

In GitHub **Settings → Environments**, create an environment named `production`.
Add these environment secrets (repository secrets also work):

| Secret | Value |
| --- | --- |
| `OCI_HOST` | VM public IPv4 address or DNS hostname, without scheme or port |
| `OCI_USER` | `deploy` |
| `OCI_SSH_PRIVATE_KEY` | Full contents of the dedicated private deployment key |
| `OCI_SSH_KNOWN_HOSTS` | Verified SSH known-hosts line for exactly `OCI_HOST` |

Obtain the host key using `ssh-keyscan -H YOUR_VM_HOST` on a trusted machine.
**Before trusting that output**, compare its fingerprint with
`sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` through the Oracle console
or another trusted VM session (select the matching Ed25519 line). Never disable
host-key verification. This workflow uses SSH port 22.

Push to `main` (or run **Actions → Test and deploy → Run workflow** on `main`).
The workflow tests the API, checks JavaScript syntax, uploads the backend over
SSH, restarts the user service, and checks its health. Pull requests only run
checks; they never receive deployment credentials. Backend deployments are
serialized so an in-progress deployment is not cancelled. No cloud accounts,
DNS records, secrets, or VMs are provisioned by this repository.

Only the backend code is uploaded. Changes to the service, Caddy config, or VM
environment require applying the corresponding setup steps again.

For troubleshooting, inspect GitHub Actions and Cloudflare deployment logs.
On the VM, as `deploy`, use `systemctl --user status mistraton` and
`journalctl --user -u mistraton -n 50`. A failed deployment reports failure but
does not automatically roll back; rerun a known-good version by reverting the
bad commit on `main`. This intentionally small, read-only demo uses Python's
standard HTTP server behind Caddy; add authentication, request limits, and a
production application server before expanding it into a public application
that handles sensitive data or significant traffic.
