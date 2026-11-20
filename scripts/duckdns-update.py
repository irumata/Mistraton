#!/usr/bin/env python3
"""Optional explicit-IP DuckDNS update; prompts without echoing the account token."""
import argparse
import getpass
import ipaddress
import re
import urllib.parse
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--subdomain", required=True, help="Only the name, without .duckdns.org")
parser.add_argument("--ip", default="130.162.187.46")
args = parser.parse_args()
if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", args.subdomain):
    parser.error("Use a single lowercase subdomain")
ipaddress.IPv4Address(args.ip)
token = getpass.getpass("DuckDNS account token (hidden): ")
query = urllib.parse.urlencode({"domains": args.subdomain, "token": token, "ip": args.ip})
try:
    with urllib.request.urlopen("https://www.duckdns.org/update?" + query, timeout=20) as response:
        result = response.read().decode().strip()
except Exception:
    raise SystemExit("DNS update request failed. Check connectivity and retry; token not logged.") from None
if result != "OK":
    raise SystemExit("DuckDNS rejected the update; check domain ownership and token.")
print(f"Updated {args.subdomain}.duckdns.org to {args.ip}")
