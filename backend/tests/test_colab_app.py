"""Tests for the Colab website backend (colab_app.py)."""

import json
import time
import base64

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

LOGIN = ("safestral", "test-only-password-" + "a" * 32)


@pytest.fixture(autouse=True)
def web_login(monkeypatch):
    monkeypatch.setenv("SENTINEL_WEB_USERNAME", LOGIN[0])
    monkeypatch.setenv("SENTINEL_WEB_PASSWORD", LOGIN[1])
    monkeypatch.setenv("SENTINEL_PUBLIC_URL", "https://safestral.dpdns.org")


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

    client = TestClient(colab_app.build_app())
    client.auth = LOGIN
    return client


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


@pytest.mark.parametrize("path", [
    "/", "/api/health", "/api/db", "/api/docs", "/api/openapi.json",
    "/sentinel/health", "/sentinel/data", "/sentinel/alerts", "/sentinel/eval",
    "/sentinel/pipeline/jobs", "/sentinel/pipeline/jobs/anything",
    "/assets/app.js", "/favicon.svg", "/unknown-route",
])
def test_every_read_requires_authentication(client, path):
    response = client.get(path, auth=None)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("method,path", [
    ("POST", "/sentinel/upload"), ("POST", "/sentinel/watch"),
    ("POST", "/sentinel/feedback"), ("POST", "/sentinel/pipeline/ingest"),
    ("POST", "/sentinel/timestral/chat"), ("OPTIONS", "/api/health"),
    ("HEAD", "/api/health"), ("DELETE", "/unknown-route"),
])
def test_every_method_requires_authentication(client, method, path):
    assert client.request(method, path, auth=None).status_code == 401


@pytest.mark.parametrize("authorization", [
    "Basic !!invalid!!", "Bearer anything", "Basic", "Basic " + "x" * 5000,
    "Basic " + base64.b64encode(b"safestral:wrong-password").decode(),
], ids=["invalid-base64", "wrong-scheme", "missing-value", "oversized", "wrong-password"])
def test_invalid_credentials_are_rejected(client, authorization):
    assert client.get("/api/health", auth=None, headers={"Authorization": authorization}).status_code == 401


def test_legacy_key_and_forged_proxy_identity_are_not_credentials(client):
    response = client.get("/sentinel/data", auth=None, headers={
        "X-API-Key": "secret", "Cf-Access-Authenticated-User-Email": "owner@example.com",
        "Cf-Access-Jwt-Assertion": "fake", "X-Forwarded-For": "127.0.0.1",
    })
    assert response.status_code == 401


def test_password_is_required_even_with_legacy_key(monkeypatch):
    monkeypatch.delenv("SENTINEL_WEB_PASSWORD")
    monkeypatch.setenv("SENTINEL_API_KEY", "legacy-key")
    import colab_app
    with pytest.raises(RuntimeError, match="SENTINEL_WEB_PASSWORD"):
        colab_app.build_app()


def test_weak_password_fails_startup(monkeypatch):
    monkeypatch.setenv("SENTINEL_WEB_PASSWORD", "short")
    import colab_app
    with pytest.raises(RuntimeError, match="SENTINEL_WEB_PASSWORD"):
        colab_app.build_app()


def test_duplicate_authorization_is_rejected(client):
    valid = "Basic " + base64.b64encode(":".join(LOGIN).encode()).decode()
    response = client.get("/api/health", auth=None, headers=[
        ("Authorization", valid), ("Authorization", valid),
    ])
    assert response.status_code == 401


def test_cross_site_writes_blocked_even_with_correct_login(client):
    assert client.post("/sentinel/pipeline/ingest", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/sentinel/pipeline/ingest", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    # Same-origin requests reach the endpoint (which rejects the unknown stage).
    assert client.post("/sentinel/pipeline/unknown", json={}, headers={"Origin": "https://safestral.dpdns.org"}).status_code == 404


def test_no_wildcard_cors(client):
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_public_http_redirects_before_password_challenge(client):
    response = client.get("http://safestral.dpdns.org/api/health?check=1", auth=None, follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "https://safestral.dpdns.org/api/health?check=1"
    assert "www-authenticate" not in response.headers


def test_https_still_requires_login_and_sets_hsts(client):
    response = client.get("https://safestral.dpdns.org/api/health", auth=None)
    assert response.status_code == 401
    assert response.headers["strict-transport-security"] == "max-age=31536000"


def test_frontend_assets_are_private_and_never_embed_keys(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><head></head><body>Safestral</body></html>")
    (dist / "assets/app.js").write_text("console.log('app')")
    (dist / ".index-keyed.html").write_text("old-secret")
    (tmp_path / "outside.txt").write_text("private-file")
    (dist / "outside-link.txt").symlink_to(tmp_path / "outside.txt")
    monkeypatch.setenv("SENTINEL_DIST_DIR", str(dist))
    monkeypatch.setenv("SENTINEL_API_KEY", "old-secret")
    import colab_app
    guarded = TestClient(colab_app.build_app())
    assert guarded.get("/").status_code == 401
    assert guarded.get("/assets/app.js").status_code == 401
    html = guarded.get("/", auth=LOGIN)
    assert html.status_code == 200
    assert "__SENTINEL_KEY__" not in html.text and "old-secret" not in html.text
    assert "no-store" in html.headers["cache-control"]
    assert guarded.get("/assets/app.js", auth=LOGIN).status_code == 200
    assert guarded.get("/.index-keyed.html", auth=LOGIN).status_code == 404
    assert guarded.get("/%2e%2e/outside.txt", auth=LOGIN).status_code == 404
    assert guarded.get("/outside-link.txt", auth=LOGIN).status_code == 404


def test_websocket_upgrade_is_guarded(monkeypatch, tmp_path):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path))
    import colab_app
    app = colab_app.build_app()

    @app.websocket("/test-ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("authenticated")
        await socket.close()

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect("/test-ws"):
            pass
    assert denied.value.code == 1008
    valid = "Basic " + base64.b64encode(":".join(LOGIN).encode()).decode()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/test-ws", headers={"Authorization": valid, "Origin": "https://evil.example"}):
            pass
    with client.websocket_connect("/test-ws", headers={"Authorization": valid, "Origin": "https://safestral.dpdns.org"}) as socket:
        assert socket.receive_text() == "authenticated"


def _make_work_src(tmp_path):
    from sentinel_loop.examples.synthetic import generate_synthetic
    from sentinel_loop.ingest.loader import ingest_directory

    src = tmp_path / "work"
    generate_synthetic(src / "raw")
    ingest_directory(src / "raw", src)  # records.jsonl
    return src


def test_archive_prepare_list_download_upload(client, tmp_path):
    src = _make_work_src(tmp_path)
    res = client.post("/sentinel/archive/prepare", json={"src": str(src)})
    assert res.status_code == 200, res.text
    manifest = res.json()
    assert manifest["file_count"] >= 4 and manifest["archive"].endswith(".zip")

    listing = client.get("/sentinel/archive").json()
    assert any(a["name"] == manifest["archive"] and a["valid"] for a in listing["archives"])

    dl = client.get("/sentinel/archive/download", params={"name": manifest["archive"]})
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/zip"
    assert len(dl.content) > 1000
    # path traversal in the name is rejected
    assert client.get("/sentinel/archive/download", params={"name": "../x.zip"}).status_code in (404, 422)

    # upload + apply: bundles extracted into <data-dir>/raw and ingested
    up = client.post(
        "/sentinel/archive/upload",
        files=[("file", (manifest["archive"], dl.content, "application/zip"))],
        data={"apply": "true"},
    )
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["ingested"] is True and body["records"] == 2
    data = client.get("/sentinel/data").json()
    assert set(data["raw_bundles"]) == {"inc-001", "inc-002"}


def test_archive_upload_rejects_garbage(client):
    up = client.post("/sentinel/archive/upload",
                     files=[("file", ("evil.zip", b"not a zip at all", "application/zip"))])
    assert up.status_code == 400
    bad_name = client.post("/sentinel/archive/upload",
                           files=[("file", ("../evil.zip", b"x", "application/zip"))])
    assert bad_name.status_code in (400, 422)


def test_pipeline_archive_stages_run_as_jobs(client, tmp_path):
    # prepare-archive defaults to the repo work/ dir; run it as a pipeline job
    res = client.post("/sentinel/pipeline/prepare-archive", json={})
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    for _ in range(120):
        job = client.get(f"/sentinel/pipeline/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.5)
    assert job["status"] == "done", job["log"]
    listing = client.get("/sentinel/archive").json()
    assert listing["archives"], "prepare-archive job must produce an archive"

    # apply-archive (no args -> newest archive) extracts + ingests it
    res = client.post("/sentinel/pipeline/apply-archive", json={})
    job_id = res.json()["job_id"]
    for _ in range(120):
        job = client.get(f"/sentinel/pipeline/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.5)
    assert job["status"] == "done", job["log"]
    data = client.get("/sentinel/data").json()
    assert len(data["records"]) >= 1
