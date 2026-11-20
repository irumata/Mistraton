"""Container-side smoke check: API -> Redis -> RQ worker -> PostgreSQL -> API."""
import json
import time
import urllib.request

from app.config import settings


def call(path, data=None):
    req = urllib.request.Request(
        "http://127.0.0.1:8000" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {settings().admin_api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


if __name__ == "__main__":
    job_id = call("/api/jobs", {"text": "deployment check"})["job_id"]
    for _ in range(60):
        result = call(f"/api/jobs/{job_id}")
        if result["status"] == "finished":
            assert result["result"] == "DEPLOYMENT CHECK"
            print("Background job completed and persisted.")
            break
        if result["status"] in {"failed", "enqueue_failed"}:
            raise RuntimeError("Background job failed")
        time.sleep(1)
    else:
        raise TimeoutError("No worker finished the smoke job in 60 seconds")
