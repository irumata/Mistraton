# Operations, recovery and scaling

Run commands on the VM as `deploy`, normally from `~/hackathon/current`. For an interactive session, connect with your original admin key and use `sudo -iu deploy`; the dedicated deployment key deliberately disables PTY. For direct one-shot commands, `ssh -i ~/.ssh/mistraton_deploy deploy@130.162.187.46 'COMMAND'` works.

## Routine checks

```bash
cd ~/hackathon/current
bash scripts/compose.sh ps
bash scripts/compose.sh logs --tail 100 api worker web
docker stats --no-stream
df -h
free -h
python3 scripts/smoke.py
```

`/api/health` is process liveness. `/api/ready` checks the migrated PostgreSQL schema and Redis. Neither proves a worker is consuming jobs; `smoke.py` submits a real job and checks its persisted result. `/api/system` is protected and reports the API container ID and queue depth. Logs rotate at 10 MB × three files per container. Redis uses AOF with one-second fsync and `noeviction`; a full queue returns errors rather than evicting jobs. Watch disk space, Redis memory and growing DB rows.

Do not print `docker compose config` or full environment inspection into shared logs: resolved values contain secrets. `bash scripts/compose.sh config --quiet` checks configuration without displaying it.

## Change worker/API capacity

Edit **`~/hackathon/shared/.env`** (not a committed file):

```dotenv
API_REPLICAS=2
WORKER_REPLICAS=3
```

Then apply on the VM:

```bash
cd ~/hackathon/current
bash scripts/up.sh
python3 scripts/smoke.py --expect-replicas 2
```

Each API replica is one Uvicorn process. Each RQ worker processes one job at a time; the default is one of each. No service has a fixed `container_name` or a published API port, so replicas do not collide. Caddy refreshes all API A records through Docker DNS every five seconds and distributes requests across them. A larger worker count increases throughput only while CPU, RAM, database connections and upstream API limits permit it. Short-lived DB connections avoid sharing connections across worker forks; for sustained traffic, add a measured connection-pool strategy or PgBouncer.

`JOB_TIMEOUT` defaults to 1,200 seconds. The worker's `stop_grace_period` is 21 minutes, allowing an active job to finish when replaced. If you raise the timeout, also increase the grace period and the deployment workflow timeout. These processes support long jobs within the configured bound, not indefinite daemons. A job can be lost or duplicated around crashes and ambiguous enqueue failures. Keep external side effects idempotent, checkpoint long work in PostgreSQL, and implement explicit retry policy before relying on it for important deliveries. RQ serializes JSON here; do not enqueue untrusted function names or deserialize pickle from untrusted Redis. [RQ worker behavior](https://python-rq.org/docs/workers/).

## Back up and copy off the VM

```bash
cd ~/hackathon/current
bash scripts/backup.sh
```

This creates a consistent custom-format PostgreSQL dump plus SHA-256 checksum in `~/hackathon/shared/backups`, with private permissions and seven-day local retention. A backup is also taken before migrating an existing deployment. It covers job payloads, statuses, results and the migration table. It is not a full VM backup and does not back up the live Redis queue, Caddy certificates or `.env`.

Set a daily local backup by editing `crontab -e` **as deploy** and adding:

```cron
17 3 * * * /bin/bash /home/deploy/hackathon/current/scripts/backup.sh >> /home/deploy/hackathon/shared/backup.log 2>&1
```

Cron uses the VM's timezone. Check its log and rotate it as needed. Local copies do not protect against VM loss. From your computer, periodically copy a specific dump and its checksum:

```bash
mkdir -p ~/Documents/mistraton-backups
scp -i ~/.ssh/mistraton_deploy \
  deploy@130.162.187.46:hackathon/shared/backups/postgres-TIMESTAMP.dump \
  deploy@130.162.187.46:hackathon/shared/backups/postgres-TIMESTAMP.dump.sha256 \
  ~/Documents/mistraton-backups/
cd ~/Documents/mistraton-backups
shasum -a 256 -c postgres-TIMESTAMP.dump.sha256
```

Replace `TIMESTAMP` with the actual filename printed by the backup. Store your `.env` in an encrypted password manager separately. Object storage, extra volumes and snapshot retention can have costs beyond their allowances; no offsite service is provisioned automatically. Your own computer is the default free offsite destination.

Named volumes are `hackathon_postgres_data`, `hackathon_redis_data`, `hackathon_caddy_data` and `hackathon_caddy_config`. Keep the project name `hackathon`; changing it creates different volumes. Never delete the original VM/boot volume until a new VM is working and a restore has been verified. Redis AOF is persistence, not an independent backup; up to about a second of Redis writes can be lost on a crash, and machine/volume loss can lose the entire queue.

## Restore after data loss

1. Disable `DEPLOY_ENABLED` during maintenance so a push cannot interrupt recovery.
2. Copy the chosen dump/checksum to the target VM, check the checksum, and make a fresh dump of any current database you might still need.
3. Ensure the target `.env` points to this stack's internal, dedicated DB/Redis. The restore script refuses external endpoint overrides.
4. On the VM:

```bash
cd ~/hackathon/current
bash scripts/restore.sh --confirm-replace \
  /home/deploy/hackathon/shared/backups/postgres-TIMESTAMP.dump
python3 scripts/smoke.py
```

**This stops web/API/workers, drops and recreates the configured database, restores the dump, and clears the stack's dedicated Redis database 0.** Recreating the database avoids leaving tables from newer migrations behind. It then reapplies current migrations and restarts the application. New jobs/results since the backup are not recovered. If a command fails, the script leaves services stopped for inspection rather than pretending recovery succeeded. It does not automatically reconcile pending jobs or external side effects.

After restoring, review unfinished DB rows:

```bash
bash scripts/compose.sh exec -T db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id,status,created_at FROM jobs WHERE status <> '\''finished'\'' ORDER BY created_at;"'
```

For a specific demo job you have decided is safe to repeat, stop workers, ensure the job is absent from Redis, and requeue:

```bash
bash scripts/compose.sh stop worker
bash scripts/compose.sh exec -T api python -m app.requeue --workers-stopped JOB_UUID
bash scripts/compose.sh up -d worker
```

The confirmation flag is a statement from the operator, not an automatic check of every worker. Never run recovery while another worker could still be performing the same external action. For real integrations, use provider idempotency keys based on a stable event/job ID. A transactional outbox and provider-event deduplication are the next step for reliable delivery; this starter's DB-then-Redis enqueue is intentionally not an atomic cross-system transaction.

Practice a restore on a separate test environment before relying on backups. CI includes an isolated container backup/restore test when you enable it.

## Roll back an application release

Keep at least the last two successful release directories and their tagged Docker images. List release IDs with `ls ~/hackathon/releases` and select a known good full commit SHA.

```bash
cd ~/hackathon/releases/PREVIOUS_FULL_COMMIT_SHA
bash scripts/compose.sh up -d --wait api worker web
bash scripts/compose.sh exec -T api python -m app.check
ln -sfn "$PWD" ~/hackathon/current.new
mv -Tf ~/hackathon/current.new ~/hackathon/current
```

Run `smoke.py` from that release afterward. This changes application images only. It never undoes schema changes or restores old data. Use additive/backward-compatible SQL migrations: add new fields first, deploy compatible code, backfill, and remove old fields only after the rollback window. Never modify an already-applied migration; add a new numbered SQL file. Migrations run under one PostgreSQL advisory lock in a transaction, before new APIs start.

## Secrets and image updates

To rotate the admin key or webhook secret, update `shared/.env` and recreate app services with `scripts/up.sh`. Old browser keys stop working; update webhook senders. To change the database password, first change the actual PostgreSQL role password in a trusted database session and then update `.env`. `POSTGRES_PASSWORD` initializes a new volume only; editing it does not rotate an existing role. URL-encode non-hex passwords if setting URLs manually. To rotate Redis credentials, change Redis and application configuration together in a maintenance window.

Python and JavaScript dependencies are locked; Python installs also verify hashes. Refresh locks intentionally, review changes and run checks. To refresh Python locks with uv from `backend/`:

```bash
uv pip compile requirements.in --python-version 3.12 --generate-hashes --upgrade -o requirements.txt
uv pip compile requirements-dev.in --python-version 3.12 --generate-hashes --upgrade -o requirements-dev.txt
```

Official container base images track supported major versions, not immutable digests. Pull/rebuild during planned maintenance and test before production; pin reviewed image digests if you need fully repeatable base images. `bash scripts/compose.sh build --pull api web` updates build bases, and `bash scripts/compose.sh pull db redis` fetches data service patch images. Never switch PostgreSQL to a different major version on the existing data volume without a supported upgrade or dump/restore. Keep old images needed for rollback. Avoid `docker system prune --volumes` and do not automatically remove old images/releases.

## Upgrade path beyond one VM

1. **Measure first.** Compare CPU/memory, queue depth, job duration, DB latency and disk use. Add replicas only where they help. Build during quieter periods on very small VMs.
2. **Resize the existing VM within the free allowance, if available.** Take a DB dump/offsite copy, inspect the Oracle cost estimate, schedule a maintenance window, change shape/memory in the Console, then restart and rerun the smoke check. Container data remains in volumes on the retained disk, but verify this and public-IP retention in your specific instance configuration. No script here changes your shape.
3. **Move to another VM when needed.** Prepare Docker and firewalls there; copy code, encrypted `.env` and verified DB dump; restore there while writes are paused; update DuckDNS, SSH host pin and `SSH_HOST`; validate; only then retire the old machine. Rebuild images on the new architecture. Do not copy a live PostgreSQL data directory between architectures/major versions.
4. **Separate data services.** The app reads `DATABASE_URL` and `REDIS_URL`, so code does not need to change when data services move. Use private networking and service credentials, PostgreSQL TLS and Redis TLS where supported. Do not expose 5432/6379 publicly. Revise Compose to remove the app's local `depends_on` entries and disable the local `db`/`redis` services. Adapt backup/restore scripts before enabling deploys; the supplied backup script deliberately refuses external DB overrides so it cannot back up the wrong database silently. Endpoint overrides alone leave the local services running.
5. **Run API/worker containers on additional machines.** Compose replica counts apply only to one Docker host. Multi-host routing needs a private network and Caddy upstream configuration/service discovery for those hosts, plus a separate deployment plan. Keep user data/files in PostgreSQL or shared object storage, not API/worker container filesystems. No shared file uploads are implemented in this starter. Add a storage abstraction before adding uploads.

A managed DB, larger VM, extra storage, load balancer, or external API may require payment. This is an architectural path, not permission to provision paid services. Keep the existing free stack until you explicitly choose otherwise.
