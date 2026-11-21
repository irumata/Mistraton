"""Tests for the Colab website backend (colab_app.py)."""

import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SENTINEL_ARTIFACTS_DIR", str(data_dir / "artifacts"))
    monkeypatch.delenv("SENTINEL_API_KEY", raising=False)
    monkeypatch.delenv("SENTINEL_DIST_DIR", raising=False)
    monkeypatch.delenv("SENTINEL_TIMESTRAL_HF", raising=False)
    import colab_app

    return TestClient(colab_app.build_app())


RECORD = {
    "incident_id": "inc-1",
    "timestamp_start": "2026-01-01T10:00:00+00:00",
    "timestamp_end": "2026-01-01T11:00:00+00:00",
    "severity_hint": "sev2",
}
CSV = "timestamp,value\n2026-01-01T09:00:00+00:00,10\n2026-01-01T10:00:00+00:00,99\n"


def test_health_and_db(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/db").json()["database"] == "sqlite"
    assert client.get("/sentinel/health").json()["status"] == "ok"


def test_upload_and_ingest(client, tmp_path):
    res = client.post(
        "/sentinel/upload",
        data={"incident_id": "inc-1", "ingest": "true"},
        files=[
            ("files", ("record.json", json.dumps(RECORD).encode(), "application/json")),
            ("files", ("x.csv", CSV.encode(), "text/csv")),
        ],
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ingested"] is True and body["records"] >= 1
    records = (tmp_path / "data" / "records.jsonl").read_text().splitlines()
    assert json.loads(records[0])["incident_id"] == "inc-1"


def test_pipeline_job_ingest(client, tmp_path):
    client.post(
        "/sentinel/upload",
        data={"incident_id": "inc-1", "ingest": "false"},
        files=[("files", ("record.json", json.dumps(RECORD).encode(), "application/json"))],
    )
    res = client.post("/sentinel/pipeline/ingest", json={})
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    for _ in range(60):
        job = client.get(f"/sentinel/pipeline/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.5)
    assert job["status"] == "done", job["log"]
    assert "ingested 1 incident records" in job["log"]


def test_pipeline_unknown_stage(client):
    assert client.post("/sentinel/pipeline/nope", json={}).status_code == 404


def test_timestral_chat_503_without_checkpoint(client):
    res = client.post("/sentinel/timestral/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert res.status_code == 503


def test_api_key_guard(tmp_path, monkeypatch):
    data_dir = tmp_path / "data2"
    data_dir.mkdir()
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SENTINEL_API_KEY", "secret")
    import colab_app

    guarded = TestClient(colab_app.build_app())
    assert guarded.post("/sentinel/pipeline/ingest", json={}).status_code == 401
    ok = guarded.post("/sentinel/pipeline/ingest", json={}, headers={"X-API-Key": "secret"})
    assert ok.status_code == 200
    # read-only endpoints stay open (the status page must work)
    assert guarded.get("/api/health").status_code == 200
