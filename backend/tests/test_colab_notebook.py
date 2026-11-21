"""Verify notebook publication fails closed without contacting Cloudflare."""

import io
import json
import os
import pathlib
import subprocess
import time
from types import SimpleNamespace

import pytest

NOTEBOOK = pathlib.Path(__file__).resolve().parents[2] / "notebooks/colab_website.ipynb"
TUNNEL_CELL = next(
    "".join(cell["source"]) for cell in json.loads(NOTEBOOK.read_text())["cells"]
    if cell.get("metadata", {}).get("id", cell.get("id")) == "198d56b8"
)


@pytest.fixture
def tunnel_harness(monkeypatch):
    calls = []
    process = SimpleNamespace(
        poll=lambda: None,
        kill=lambda: calls.append("killed"),
        wait=lambda **kwargs: 0,
    )

    def start(args, **kwargs):
        calls.append((args, kwargs["env"]))
        return process

    monkeypatch.setattr(subprocess, "Popen", start)
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    monkeypatch.setenv("SENTINEL_WEB_PASSWORD", "must-not-be-given-to-cloudflared")
    monkeypatch.setenv("MISTRAL_API_KEY", "must-not-be-given-to-cloudflared-either")
    namespace = {
        "os": os,
        "userdata": SimpleNamespace(get=lambda _: "test-connector-token"),
        "PUBLIC_URL": "https://safestral.dpdns.org",
        "WEB_USERNAME": "safestral",
        "PROTECTED_PATHS": ["/", "/api/health", "/sentinel/data"],
        "open": lambda *args, **kwargs: io.BytesIO(),
    }
    return namespace, calls


def test_no_connector_starts_for_an_unprotected_local_backend(tunnel_harness):
    namespace, calls = tunnel_harness
    namespace["_status"] = lambda *args, **kwargs: 200
    with pytest.raises(RuntimeError, match="anonymous access"):
        exec(TUNNEL_CELL, namespace)
    assert calls == []


def test_no_connector_starts_if_login_does_not_work(tunnel_harness):
    namespace, calls = tunnel_harness
    namespace["_status"] = lambda *args, **kwargs: 401
    with pytest.raises(RuntimeError, match="login failed"):
        exec(TUNNEL_CELL, namespace)
    assert calls == []


def test_anonymous_public_success_closes_the_connector(tunnel_harness):
    namespace, calls = tunnel_harness

    def status(url, *, local_login=False):
        if url.startswith("http://127.0.0.1:8000/"):
            return 200 if local_login else 401
        return 200

    namespace["_status"] = status
    with pytest.raises(RuntimeError, match="permits anonymous access"):
        exec(TUNNEL_CELL, namespace)
    assert calls[-1] == "killed"
    args, env = calls[0]
    assert "--url" not in args and "test-connector-token" not in args
    assert env["TUNNEL_TOKEN"] == "test-connector-token"
    assert "SENTINEL_WEB_PASSWORD" not in env and "MISTRAL_API_KEY" not in env


def test_failed_public_path_closes_the_connector(tunnel_harness):
    namespace, calls = tunnel_harness

    def status(url, *, local_login=False):
        if local_login:
            return 200
        if url == "https://safestral.dpdns.org/sentinel/data":
            return 200
        return 401

    namespace["_status"] = status
    with pytest.raises(RuntimeError, match="Public authentication check failed"):
        exec(TUNNEL_CELL, namespace)
    assert calls[-1] == "killed"
