#!/usr/bin/env python3
"""Check the public route and a real background job, without printing credentials."""
import argparse
import json
import time
import urllib.error
import urllib.request

from env import read_env

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--env", default=".env")
parser.add_argument("--url")
parser.add_argument("--expect-replicas", type=int, default=1)
args = parser.parse_args()
env = read_env(args.env)
base = (args.url or (env["DOMAIN"] if env["DOMAIN"].startswith("http") else "https://" + env["DOMAIN"])).rstrip("/")
if not args.url and base == "http://localhost":
    base += ":" + env.get("HTTP_PORT", "8080")


def call(path, data=None, auth=True):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = "Bearer " + env["ADMIN_API_KEY"]
    request = urllib.request.Request(base + path, data=json.dumps(data).encode() if data else None, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


with urllib.request.urlopen(base + "/", timeout=15) as response:
    assert 'id="root"' in response.read().decode(), "Frontend missing"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
assert call("/api/ready", auth=False)["status"] == "ready"
try:
    call("/api/jobs", {"text": "must be rejected"}, auth=False)
except urllib.error.HTTPError as error:
    assert error.code == 401
else:
    raise AssertionError("Unauthenticated job was accepted")
job_id = call("/api/jobs", {"text": "hello from smoke check"})["job_id"]
for _ in range(60):
    job = call("/api/jobs/" + job_id)
    if job["status"] == "finished":
        assert job["result"] == "HELLO FROM SMOKE CHECK"
        break
    assert job["status"] not in {"failed", "enqueue_failed"}, job["status"]
    time.sleep(1)
else:
    raise TimeoutError("Background job did not complete")
instances = set()
for _ in range(max(20, args.expect_replicas * 10)):
    instances.add(call("/api/system")["instance"])
    if len(instances) >= args.expect_replicas:
        break
    time.sleep(0.3)
assert len(instances) >= args.expect_replicas, f"Reached only {len(instances)} API replicas"
print(f"PASS: frontend, authentication, readiness, background job, persisted result, {len(instances)} API replica(s)")
