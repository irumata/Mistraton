# Validation record and repeatable checks

Prepared 20 November 2026 for Oracle IP **130.162.187.46** and repository **irumata/Mistraton**.

## Checks completed in the build workspace

- TypeScript type check and Vite production build.
- Seven FastAPI tests covering public health, protected routes, payload bounds, accepted submission, signed webhook verification/time window, disabled docs and malformed admin authorization.
- Ruff checks on backend and helper Python scripts; Bash syntax checks on shell scripts.
- GitHub workflow syntax and expression checks with actionlint.
- Compose parsed with the official Compose executable, including replica settings and verification that DB, Redis, API and worker expose no host ports.
- Caddyfile parsed and validated with the official Caddy executable.
- **Actual native integration**, using temporary PostgreSQL and Redis, the default RQ Worker, Uvicorn and Caddy: repeated migrations, authenticated submit → queue → worker → PostgreSQL result → polling; frontend serving and headers; unauthenticated rejection; signed webhook accepted.
- A real custom-format PostgreSQL dump restored into a separate temporary database with completed job results intact.
- A real Redis outage returned HTTP 503 with an inspectable job ID and retained `enqueue_failed` status in PostgreSQL.
- Frontend npm audit at the time of the build reported no known dependency vulnerabilities. This is time-specific and not a security guarantee.

No Docker engine was available on the build computer. The native integration used local process binaries, with a temporary Caddy configuration pointing at loopback instead of Docker DNS. **Linux image builds, Docker DNS discovery across replicas, Compose restart behavior, and the shell backup/restore wrapper in containers have not been executed here.** The supplied CI workflow runs those checks after `CI_ENABLED=true` is set. CI is initially skipped for this private repository to avoid consuming an unknown paid allowance.

No SSH connection to the Oracle VM was attempted. Its OS, resources, billing eligibility, firewall, DNS ownership and live HTTPS are unverified. No DuckDNS subdomain was claimed or updated. The final live checks are in [SETUP.md](SETUP.md).

## Repeat checks locally

Use Python 3.12 for backend development and Node 24 for the frontend:

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/pytest -q
cd ../frontend
npm ci
npm run build
npm audit
cd ..
for script in scripts/*.sh; do bash -n "$script"; done
```

For the container checks, use an isolated local stack (no existing production volumes). Generate local config, set `API_REPLICAS=2` and `WORKER_REPLICAS=2` in it, then:

```bash
python3 scripts/configure.py --local
bash scripts/up.sh
python3 scripts/smoke.py --expect-replicas 2
```

Verify backup/restore only against disposable local data or a planned maintenance copy; the restore command replaces the database and clears the dedicated Redis queue. CI uses temporary containers to do that automatically. Never run the CI cleanup command `down --volumes` on production.

CI tests AMD64 Linux images. Production builds natively on the VM; an Arm64 A1 VM is supported by the chosen official image families, but the first VM build is still the verification for your exact host. If a wheel/image fails on your architecture, stop and inspect the build rather than changing to a paid shape automatically.
