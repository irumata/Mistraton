import json

import pytest
from fastapi.testclient import TestClient

from sentinel_loop.config import resolve_config
from sentinel_loop.dataset.assemble import build_dataset, load_metric_series
from sentinel_loop.extract_metrics.extractor import extract_all
from sentinel_loop.labeling.store import LabelingStore
from sentinel_loop.mistral_client import MistralClients
from sentinel_loop.schemas import WindowState
from sentinel_loop.serve.watcher import create_app
from sentinel_loop.tuning.train import train
from .conftest import make_mock_transport


@pytest.fixture
def served(data_dir, config, mock_clients, settings):
    extract_all(data_dir, mock_clients, config)
    cfg, _ = resolve_config(data_dir, None)
    build_dataset(data_dir, cfg)
    det = train(data_dir, cfg, strategy="forecasting", artifacts_dir=settings.artifacts_dir)
    store = LabelingStore(settings.db_path)
    app = create_app(detector=det, config=cfg, store=store, clients=mock_clients, settings=settings)
    return TestClient(app), data_dir, store, settings


def _streams(data_dir):
    series = load_metric_series(data_dir)
    return {
        metric: [{"ts": p.ts.isoformat(), "value": p.value} for p in points]
        for metric, points in series.items()
    }


def test_health(served):
    client, *_ = served
    assert client.get("/sentinel/health").json()["status"] == "ok"


def test_watch_detects_incident(served):
    client, data_dir, store, settings = served
    resp = client.post("/sentinel/watch", json={"streams": _streams(data_dir), "shadow": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shadow"] is True
    assert body["events"], "expected detection events on the synthetic stream"
    states = {e["window_state"] for e in body["events"]}
    assert states & {WindowState.incident.value, WindowState.precursor.value}
    for e in body["events"]:
        assert set(e) >= {"metric", "model_confidence", "window_state", "lead_time_minutes"}
    # shadow mode: alerts logged but not verified/sent; Large explanation attached
    for a in body["alerts"]:
        assert a["verified"] is None
        assert a["explanation"], "expected a printed Large explanation on shadow alerts"
    # every emitted window carries its chain trace
    for c in body["chains"]:
        assert [s["stage"] for s in c["stages"]][0] == "timstral"


def test_watch_chain_order_and_explanation(served, capsys):
    """Fixed chain: timstral -> shieldstral -> large; explanation printed."""
    client, data_dir, *_ = served
    resp = client.post("/sentinel/watch", json={"streams": _streams(data_dir)})  # non-shadow
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["alerts"], "mock Shieldstral says YES, alerts should fire"
    fired = [c for c in body["chains"] if c["fired"]]
    assert fired
    for c in fired:
        stages = [s["stage"] for s in c["stages"]]
        assert stages == ["timstral", "shieldstral", "large"]
        assert c["alert"]["verified"] is True
        assert "SENTINEL-LOOP ALERT" in c["alert"]["explanation"]
        assert c["explanation"]["summary"]
    out = capsys.readouterr().out
    assert "SENTINEL-LOOP ALERT" in out, "explanation must be printed"
    assert "WHY TIMESTRAL FLAGGED" in out


def test_watch_suppresses_unverified(served, data_dir, settings, config):
    """Shieldstral NO -> candidate suppressed as false positive, no Large call."""
    client, *_ = served
    rejecting = MistralClients(transport=make_mock_transport(shieldstral_verdict="NO"))
    from sentinel_loop.tuning.strategies import load_detector
    from pathlib import Path
    det = load_detector(Path(settings.artifacts_dir) / "detector_forecasting.json")
    store = LabelingStore(settings.db_path)
    app = create_app(detector=det, config=config, store=store, clients=rejecting, settings=settings)
    body = TestClient(app).post("/sentinel/watch", json={"streams": _streams(data_dir)}).json()
    assert body["events"], "timstral still emits candidate events"
    assert body["alerts"] == [], "unverified candidates must not fire"
    assert body["suppressed"] >= 1
    for c in body["chains"]:
        if c["suppressed"]:
            stages = [s["stage"] for s in c["stages"]]
            assert stages == ["timstral", "shieldstral"], "no Large explanation for suppressed candidates"
            assert c["explanation"] is None


def test_watch_dedups_repeated_calls(served):
    client, data_dir, *_ = served
    streams = _streams(data_dir)
    first = client.post("/sentinel/watch", json={"streams": streams}).json()
    second = client.post("/sentinel/watch", json={"streams": streams}).json()
    assert len(second["events"]) == 0  # same windows already emitted
    assert len(first["events"]) > 0


def test_feedback_flow(served):
    client, data_dir, store, settings = served
    body = client.post("/sentinel/watch", json={"streams": _streams(data_dir)}).json()
    alert = body["alerts"][0]
    resp = client.post("/sentinel/feedback", json={
        "alert_id": alert["alert_id"], "decision": "confirmed", "reviewer": "oncall",
    })
    assert resp.status_code == 200, resp.text
    saved = store.get_alert(alert["alert_id"])
    assert saved.verified is True and saved.reviewer == "oncall"
    precision, n = store.reviewed_precision()
    assert n == 1 and precision == 1.0

    bad = client.post("/sentinel/feedback", json={
        "alert_id": "missing", "decision": "confirmed", "reviewer": "x",
    })
    assert bad.status_code == 404


def test_watch_unknown_metric_ignored(served):
    client, *_ = served
    resp = client.post("/sentinel/watch", json={"streams": {"nope": [{"ts": "2026-01-10T00:00:00+00:00", "value": 1.0}]}})
    assert resp.status_code == 200
    assert resp.json()["events"] == []
