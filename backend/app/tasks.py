from uuid import UUID

from app.db import connection


def process(job_id: str) -> str:
    """Demo is safe to retry: deterministic result and an idempotent DB update.

    External side effects need provider idempotency keys before enabling retries.
    """
    with connection() as conn:
        row = conn.execute("SELECT text, status, result FROM jobs WHERE id=%s", (UUID(job_id),)).fetchone()
        if row is None:
            raise ValueError("Job row not found")
        if row["status"] == "finished":
            return row["result"]
        conn.execute("UPDATE jobs SET status='running', updated_at=now() WHERE id=%s", (UUID(job_id),))
    try:
        # Replace with bounded external API calls; never accept arbitrary Python functions or URLs.
        result = row["text"].upper()
        with connection() as conn:
            conn.execute(
                "UPDATE jobs SET status='finished', result=%s, updated_at=now() WHERE id=%s",
                (result, UUID(job_id)),
            )
        return result
    except Exception:
        with connection() as conn:
            conn.execute("UPDATE jobs SET status='failed', updated_at=now() WHERE id=%s", (UUID(job_id),))
        raise
