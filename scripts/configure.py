#!/usr/bin/env python3
"""Create an untracked .env with independent random secrets. Never overwrite one."""
import argparse
import os
import re
import secrets
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--local", action="store_true")
parser.add_argument("--domain")
parser.add_argument("--email")
parser.add_argument("--output", default=".env")
args = parser.parse_args()
if not args.local and (not args.domain or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}\.duckdns\.org", args.domain)):
    parser.error("Use --local or --domain YOURNAME.duckdns.org (lowercase, no https://)")
if not args.local and (not args.email or not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", args.email)):
    parser.error("Supply --email YOUR_EMAIL for certificate account notices")
path = Path(args.output)
values = {
    "DOMAIN": "http://localhost" if args.local else args.domain,
    "ACME_EMAIL": "local@example.invalid" if args.local else args.email,
    "POSTGRES_USER": "hackathon", "POSTGRES_DB": "hackathon",
    "POSTGRES_PASSWORD": secrets.token_hex(24), "REDIS_PASSWORD": secrets.token_hex(24),
    "ADMIN_API_KEY": secrets.token_hex(32), "WEBHOOK_SECRET": secrets.token_hex(32),
    "API_REPLICAS": "1", "WORKER_REPLICAS": "1", "JOB_TIMEOUT": "1200", "REDIS_MAXMEMORY": "256mb",
    "HTTP_BIND": "127.0.0.1" if args.local else "0.0.0.0",
    "HTTP_PORT": "8080" if args.local else "80", "HTTPS_PORT": "8443" if args.local else "443",
}
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    parser.error(f"{path} already exists; edit it instead of rotating secrets accidentally")
with os.fdopen(fd, "w") as stream:
    stream.write("# Private runtime configuration. Do not commit or source this file.\n")
    stream.writelines(f"{key}={value}\n" for key, value in values.items())
print(f"Created {path} with private permissions; no secrets printed.")
