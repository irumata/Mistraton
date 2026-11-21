"""Explicit recovery of one job after Redis loss. Stop all workers before using."""
import argparse
from uuid import UUID

from app.config import settings
from app.db import connection
from app.queue import queue

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers-stopped", action="store_true", required=True)
    parser.add_argument("job_id", type=UUID)
    args = parser.parse_args()
    with connection() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=%s FOR UPDATE", (args.job_id,)).fetchone()
        if not row or row["status"] == "finished":
            parser.error("Job missing or already finished")
        existing = queue().fetch_job(str(args.job_id))
        if existing:
            parser.error("Job still exists in Redis; inspect it before recovery")
        queue().enqueue("app.tasks.process", str(args.job_id), job_id=str(args.job_id), job_timeout=settings().job_timeout)
        conn.execute("UPDATE jobs SET status='queued', updated_at=now() WHERE id=%s", (args.job_id,))
    print(f"Requeued {args.job_id}")
