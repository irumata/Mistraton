import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from sentinel_loop.cli import main as cli_main
from sentinel_loop.config import resolve_config
from sentinel_loop.dataset.assemble import build_dataset, load_metric_series, resample_series
from sentinel_loop.examples.synthetic import T0
from sentinel_loop.extract_metrics.extractor import extract_all
from sentinel_loop.labeling.store import LabelingStore
from sentinel_loop.serve.watcher import create_app
from sentinel_loop.timestral import LocalTimestralBackend
from sentinel_loop.tuning.forecast import forecast_report
from sentinel_loop.tuning.train import train


@pytest.fixture
def prepared(data_dir, config, mock_clients):
    """ingest (data_dir fixture) -> extract (mocked) -> merged metrics.yaml -> dataset."""
    extract_all(data_dir, mock_clients, config)
    cfg, _ = resolve_config(data_dir, None)
    build_dataset(data_dir, cfg)
    return cfg


@pytest.fixture
def detector(data_dir, prepared):
    return train(data_dir, prepared, strategy="forecasting", artifacts_dir=data_dir / "artifacts")


def _cut_series(data_dir, metric: str, upto):
    series = load_metric_series(data_dir)
    grid, values = resample_series(series[metric], 60)
    pairs = [(t, v) for t, v in zip(grid, values) if t <= upto]
    return [v for _, v in pairs], pairs[-1][0]


def test_backend_band_widens_with_horizon():
    backend = LocalTimestralBackend(horizon=5, band_k=3.0)
    backend.forecast["m"] = {"sigma": 2.0, "q05": -1.0, "q95": 1.0}
    out = backend.forecast_horizon("m", [10.0] * 20, 4)
    assert len(out) == 4
    widths = [hi - lo for _, lo, hi in out]
    assert widths == sorted(widths) and widths[-1] > widths[0]
    # flat history -> flat prediction
    assert all(p == pytest.approx(10.0) for p, _, _ in out)


def test_forecast_flags_upcoming_breach(data_dir, detector):
    # inc-001: start T0+3h, 25min lead ramp -> cut 10min into the ramp so the
    # trailing slope extrapolates the rise; the forecast should breach the band.
    ramp_start = T0 + timedelta(hours=3) - timedelta(minutes=25)
    values, last_ts = _cut_series(data_dir, "api_latency_ms", ramp_start + timedelta(minutes=10))
    points, issue = detector.forecast("api_latency_ms", values, steps=40, step_seconds=60, last_ts=last_ts)
    assert len(points) == 40
    assert points[0].step == 1 and points[-1].step == 40
    assert issue is not None
    assert issue.metric == "api_latency_ms"
    assert issue.expected_in_minutes > 0
    assert issue.steps_ahead >= 1
    assert issue.expected_breach_ts is not None
    assert 0.0 <= issue.model_confidence <= 1.0


def test_forecast_no_issue_on_normal_segment(data_dir, detector):
    values, last_ts = _cut_series(data_dir, "api_latency_ms", T0 + timedelta(hours=1))
    points, issue = detector.forecast("api_latency_ms", values, steps=30, step_seconds=60, last_ts=last_ts)
    assert len(points) == 30
    assert issue is None


def test_forecast_report_structure(data_dir, prepared, detector):
    report = forecast_report(data_dir, prepared, detector, steps=6)
    assert report["strategy"] == "forecasting"
    assert report["steps"] == 6
    assert set(report["forecasts"]) >= {"api_latency_ms", "error_rate"}
    for pts in report["forecasts"].values():
        assert len(pts) == 6
        assert set(pts[0]) >= {"step", "predicted", "lower", "upper", "ts"}
    assert isinstance(report["issues"], list)


@pytest.fixture
def served(data_dir, prepared, settings, detector):
    store = LabelingStore(settings.db_path)
    app = create_app(detector=detector, config=prepared, store=store, clients=None, settings=settings)
    return TestClient(app), data_dir


def _streams(data_dir):
    series = load_metric_series(data_dir)
    return {
        metric: [{"ts": p.ts.isoformat(), "value": p.value} for p in points]
        for metric, points in series.items()
    }


def test_forecast_endpoint(served):
    client, data_dir = served
    resp = client.post("/sentinel/forecast", json={"streams": _streams(data_dir), "steps": 8})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["strategy"] == "forecasting" and body["steps"] == 8
    assert set(body["forecasts"]) >= {"api_latency_ms", "error_rate"}
    assert len(body["forecasts"]["api_latency_ms"]) == 8
    assert isinstance(body["issues"], list)
    for issue in body["issues"]:
        assert set(issue) >= {"metric", "steps_ahead", "expected_in_minutes", "model_confidence"}


def test_forecast_endpoint_unknown_metric_ignored(served):
    client, _ = served
    resp = client.post("/sentinel/forecast", json={
        "streams": {"nope": [{"ts": "2026-01-10T00:00:00+00:00", "value": 1.0}]}, "steps": 4,
    })
    assert resp.status_code == 200
    assert resp.json()["forecasts"] == {}


def test_cli_forecast(data_dir, prepared, detector, capsys):
    rc = cli_main(["forecast", "--data-dir", str(data_dir), "--strategy", "forecasting", "--steps", "5"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["strategy"] == "forecasting" and report["steps"] == 5
    assert "forecasts" in report and "issues" in report
