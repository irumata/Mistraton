import base64
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from voice import DEFAULT_MODEL, create_voice_router


class FakeSpeech:
    def __init__(self, payload=b"FAKE-MP3", error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(audio_data=base64.b64encode(self.payload).decode())


def make_client(monkeypatch, speech, api_key="test-key"):
    if api_key:
        monkeypatch.setenv("MISTRAL_API_KEY", api_key)
    else:
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("VOICE_MODEL", raising=False)
    monkeypatch.delenv("VOICE_DEFAULT", raising=False)
    app = FastAPI()
    app.include_router(create_voice_router(client_factory=lambda key: SimpleNamespace(audio=SimpleNamespace(speech=speech))), prefix="/api/voice")
    return TestClient(app)


def test_status_disabled_without_key(monkeypatch):
    client = make_client(monkeypatch, FakeSpeech(), api_key="")
    response = client.get("/api/voice/status")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "voice": {"enabled": False, "model": DEFAULT_MODEL, "voice_id": None}}


def test_status_enabled_with_defaults(monkeypatch):
    client = make_client(monkeypatch, FakeSpeech())
    monkeypatch.setenv("VOICE_DEFAULT", "voice_bea_cribella")
    assert client.get("/api/voice/status").json()["voice"] == {
        "enabled": True, "model": DEFAULT_MODEL, "voice_id": "voice_bea_cribella",
    }


def test_say_returns_audio_and_uses_defaults(monkeypatch):
    speech = FakeSpeech(payload=b"AUDIO-BYTES")
    client = make_client(monkeypatch, speech)
    monkeypatch.setenv("VOICE_MODEL", "voxtral-mini-tts-3-0")
    monkeypatch.setenv("VOICE_DEFAULT", "voice_bea_cribella")
    response = client.post("/api/voice/say", json={"text": "Hello from Bea Cribella"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"AUDIO-BYTES"
    assert speech.calls == [{
        "model": "voxtral-mini-tts-3-0",
        "input": "Hello from Bea Cribella",
        "response_format": "mp3",
        "voice_id": "voice_bea_cribella",
    }]


def test_say_request_overrides(monkeypatch):
    speech = FakeSpeech()
    client = make_client(monkeypatch, speech)
    response = client.post("/api/voice/say", json={"text": "Hi", "voice_id": "voice_other", "model": "voxtral-tts-2603"})
    assert response.status_code == 200
    assert speech.calls[0]["voice_id"] == "voice_other"
    assert speech.calls[0]["model"] == "voxtral-tts-2603"


def test_say_without_key_is_503(monkeypatch):
    client = make_client(monkeypatch, FakeSpeech(), api_key="")
    response = client.post("/api/voice/say", json={"text": "hello"})
    assert response.status_code == 503
    assert "MISTRAL_API_KEY" in response.json()["detail"]


def test_say_upstream_failure_is_502_without_details(monkeypatch):
    speech = FakeSpeech(error=RuntimeError("api_key=SECRET upstream detail"))
    client = make_client(monkeypatch, speech)
    response = client.post("/api/voice/say", json={"text": "hello"})
    assert response.status_code == 502
    assert response.json() == {"detail": "Voice synthesis failed"}
    assert "SECRET" not in response.text


def test_say_validates_text(monkeypatch):
    client = make_client(monkeypatch, FakeSpeech())
    assert client.post("/api/voice/say", json={"text": ""}).status_code == 422
    assert client.post("/api/voice/say", json={"text": "x" * 4001}).status_code == 422


def test_router_is_mounted_on_main_app():
    import main

    paths = {route.path for route in main.app.routes}
    assert "/api/voice/status" in paths
    assert "/api/voice/say" in paths


@pytest.mark.parametrize("payload_shape", ["audio_data", "data_b64", "data_bytes", "raw_bytes"])
def test_extract_audio_shapes(monkeypatch, payload_shape):
    raw = b"RAW"
    if payload_shape == "audio_data":
        response = SimpleNamespace(audio_data=base64.b64encode(raw).decode())
    elif payload_shape == "data_b64":
        response = SimpleNamespace(data=base64.b64encode(raw).decode())
    elif payload_shape == "data_bytes":
        response = SimpleNamespace(data=raw)
    else:
        response = raw
    speech = FakeSpeech()
    speech.complete = lambda **kwargs: response
    client = make_client(monkeypatch, speech)
    assert client.post("/api/voice/say", json={"text": "hi"}).content == raw
