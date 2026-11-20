import hashlib
import hmac
import time
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, require_admin


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setenv("ADMIN_API_KEY", "a" * 48)
    monkeypatch.setenv("WEBHOOK_SECRET", "b" * 48)
    settings.cache_clear()
    yield
    settings.cache_clear()


client = TestClient(app)
AUTH = {"Authorization": "Bearer " + "a" * 48}


def test_liveness_and_protected_routes():
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.post("/api/jobs", json={"text": "hello"}).status_code == 401
    assert client.get("/api/jobs/" + str(UUID(int=1))).status_code == 401
    assert client.get("/api/system").status_code == 401


@pytest.mark.parametrize("text", ["", "x" * 4001])
def test_payload_limit(text):
    assert client.post("/api/jobs", headers=AUTH, json={"text": text}).status_code == 422


def test_enqueue_returns_accepted(monkeypatch):
    monkeypatch.setattr("app.main.enqueue", lambda payload, job_id: {"job_id": str(job_id), "status": "queued"})
    result = client.post("/api/jobs", headers=AUTH, json={"text": "hello"})
    assert result.status_code == 202
    assert UUID(result.json()["job_id"])


def test_webhook_signature_and_time_window(monkeypatch):
    monkeypatch.setattr("app.main.enqueue", lambda payload, job_id: {"job_id": str(job_id), "status": "queued"})
    body = b'{"text":"signed message"}'
    timestamp = str(int(time.time()))
    signature = hmac.new(b"b" * 48, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    headers = {"X-Webhook-Timestamp": timestamp, "X-Webhook-Signature": signature}
    assert client.post("/webhooks/demo", content=body, headers=headers).status_code == 202
    assert client.post("/webhooks/demo", content=body + b" ", headers=headers).status_code == 401
    headers["X-Webhook-Timestamp"] = "1"
    assert client.post("/webhooks/demo", content=body, headers=headers).status_code == 401
    assert client.post("/webhooks/demo", content=body).status_code == 401


def test_docs_disabled():
    assert client.get("/docs").status_code == 404


def test_non_ascii_admin_header_is_rejected():
    with pytest.raises(HTTPException) as error:
        require_admin("Bearer invalid-é")
    assert error.value.status_code == 401
