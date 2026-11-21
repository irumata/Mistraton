import hashlib
import hmac
import logging
import os
import time
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from redis.exceptions import RedisError
from rq.exceptions import NoSuchJobError
from rq.job import Job
from rq.serializers import JSONSerializer

from app.config import settings
from app.db import connection
from app.queue import queue, redis_connection

log = logging.getLogger(__name__)
app = FastAPI(title="Hackathon API", docs_url=None, redoc_url=None, openapi_url=None)


def require_admin(authorization: str = Header(default="")):
    if not hmac.compare_digest(authorization.encode(), f"Bearer {settings().admin_api_key}".encode()):
        raise HTTPException(401, "A valid Bearer API key is required")


class JobRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/ready")
def ready():
    try:
        with connection() as conn:
            conn.execute("SELECT 1 FROM jobs LIMIT 1")
        redis_connection().ping()
        return {"status": "ready"}
    except (psycopg.Error, RedisError):
        raise HTTPException(503, "Database or queue unavailable") from None


def enqueue(payload: JobRequest, job_id: UUID):
    # DB first, then Redis. A queue outage is explicit, never a false success.
    with connection() as conn:
        conn.execute("INSERT INTO jobs (id, text, status) VALUES (%s, %s, 'queued')", (job_id, payload.text))
    try:
        queue().enqueue(
            "app.tasks.process", str(job_id), job_id=str(job_id),
            job_timeout=settings().job_timeout, result_ttl=86400, failure_ttl=604800,
        )
    except RedisError:
        # If Redis accepted the job but the response was lost, the worker can still finish it.
        with connection() as conn:
            conn.execute(
                "UPDATE jobs SET status='enqueue_failed', updated_at=now() WHERE id=%s AND status='queued'",
                (job_id,),
            )
        log.exception("Queue submission failed for %s", job_id)
        raise HTTPException(503, "Queue submission uncertain; check job_id before retrying", headers={"X-Job-ID": str(job_id)}) from None
    return {"job_id": str(job_id), "status": "queued"}


@app.post("/api/jobs", status_code=202, dependencies=[Depends(require_admin)])
def submit_job(payload: JobRequest):
    return enqueue(payload, uuid4())


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_admin)])
def get_job(job_id: UUID):
    with connection() as conn:
        row = conn.execute(
            "SELECT id, status, result, created_at, updated_at FROM jobs WHERE id=%s", (job_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Job not found")
    if row["status"] not in {"finished", "failed"}:
        try:
            job = Job.fetch(str(job_id), connection=redis_connection(), serializer=JSONSerializer)
            # Reconcile failures outside task code, e.g. a hard worker timeout.
            status = job.get_status(refresh=True)
            if status and status.value in {"failed", "stopped", "canceled"}:
                with connection() as conn:
                    updated = conn.execute(
                        "UPDATE jobs SET status='failed', updated_at=now() WHERE id=%s AND status!='finished' "
                        "RETURNING id, status, result, created_at, updated_at", (job_id,),
                    ).fetchone()
                    if updated:
                        row = updated
        except (NoSuchJobError, RedisError):
            # Persisted DB state survives Redis result expiry or an outage.
            pass
    return row


@app.get("/api/system", dependencies=[Depends(require_admin)])
def system():
    # Enables checking that Caddy actually reaches more than one API replica.
    return {"instance": os.getenv("HOSTNAME", "local"), "queue_length": len(queue())}


@app.post("/webhooks/demo", status_code=202)
async def webhook(request: Request, x_webhook_timestamp: str = Header(default=""), x_webhook_signature: str = Header(default="")):
    # Demo protocol: HMAC-SHA256(timestamp + '.' + exact raw JSON body), five-minute replay window.
    try:
        timestamp = int(x_webhook_timestamp)
    except ValueError:
        raise HTTPException(401, "Invalid webhook timestamp") from None
    if abs(time.time() - timestamp) > 300:
        raise HTTPException(401, "Expired webhook timestamp")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16384:
            raise HTTPException(413, "Webhook too large")
    signature = hmac.new(settings().webhook_secret.encode(), x_webhook_timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(x_webhook_signature.encode(), signature.encode()):
        raise HTTPException(401, "Invalid webhook signature")
    try:
        payload = JobRequest.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "Expected JSON with text between 1 and 4000 characters") from None
    # Sync DB/Redis work runs off the event loop.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(enqueue, payload, uuid4())
