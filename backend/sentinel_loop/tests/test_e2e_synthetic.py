"""End-to-end: synthetic example through the whole pipeline with mocked APIs.

ingest -> extract-metrics -> label-windows -> build-dataset -> train-detection
-> evaluate -> serve/shadow-run -> feedback, on 2 synthetic metrics + 2 incidents.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sentinel_loop.config import resolve_config
from sentinel_loop.dataset.assemble import build_dataset, load_metric_series, load_splits, load_windows
from sentinel_loop.examples.synthetic import generate_synthetic
from sentinel_loop.extract_metrics.extractor import extract_all
from sentinel_loop.feedback.loop import check_drift_and_trigger, record_review
from sentinel_loop.ingest.loader import ingest_directory
from sentinel_loop.labeling.labeler import label_all_windows
from sentinel_loop.labeling.store import LabelingStore
from sentinel_loop.schemas import WindowState
from sentinel_loop.serve.watcher import create_app
from sentinel_loop.tuning.evaluate import evaluate_strategy
from sentinel_loop.tuning.strategies import load_detector
from sentinel_loop.tuning.train import train


def test_full_pipeline(tmp_path, mock_clients):
    data_dir = tmp_path / "data"
    generate_synthetic(data_dir / "raw")

    # 1. ingest
    records = ingest_directory(data_dir / "raw", data_dir)
    assert len(records) == 2

    # 2. extract-metrics (Mistral Large, mocked)
    cfg0, _ = resolve_config(data_dir, None)
    outputs = extract_all(data_dir, mock_clients, cfg0)
    assert len(outputs) == 2
    cfg, _ = resolve_config(data_dir, None)  # merged metrics.yaml
    assert {m.name for m in cfg.metrics} == {"api_latency_ms", "error_rate"}

    # 3. label-windows (Shieldstral, mocked)
    db = data_dir / "labels.db"
    windows = label_all_windows(data_dir, db, cfg, mock_clients)
    assert any(w.label == WindowState.incident for w in windows)

    # 4. build-dataset
    res = build_dataset(data_dir, cfg)
    splits = load_splits(res["splits"])
    assert splits["train"] and splits["test"]
    all_windows = load_windows(res["windows"])
    assert len(all_windows) > 100

    # 5+6. train (all three strategies) and evaluate on held-out incidents
    reports = {}
    for strategy in ("forecasting", "embedding", "zero-shot"):
        det = train(data_dir, cfg, strategy=strategy, artifacts_dir=data_dir / "artifacts")
        reports[strategy] = evaluate_strategy(det, data_dir, cfg, split="test")
    assert reports["forecasting"].per_class["incident"].recall > 0
    assert reports["zero-shot"].per_class["incident"].recall > 0
    # tuned forecasting detector must beat or match zero-shot F1 on incident
    f1_tuned = reports["forecasting"].per_class["incident"].f1
    f1_zs = reports["zero-shot"].per_class["incident"].f1
    assert f1_tuned >= f1_zs
    assert reports["forecasting"].mean_lead_time_minutes > 0

    # 7. serve: shadow-run over the live stream via the FastAPI watcher
    det = load_detector(data_dir / "artifacts" / "detector_forecasting.json")
    store = LabelingStore(db)
    from sentinel_loop.config import Settings

    settings = Settings(data_dir=str(data_dir), db_path=str(db), artifacts_dir=str(data_dir / "artifacts"))
    app = create_app(detector=det, config=cfg, store=store, clients=None, settings=settings)
    client = TestClient(app)
    series = load_metric_series(data_dir)
    streams = {m: [{"ts": p.ts.isoformat(), "value": p.value} for p in pts] for m, pts in series.items()}
    resp = client.post("/sentinel/watch", json={"streams": streams, "shadow": True})
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert events, "watcher must emit events on the synthetic incident stream"

    # 8. feedback: confirm/reject alerts, drift trigger bookkeeping
    alerts = resp.json()["alerts"]
    assert alerts
    for i, a in enumerate(alerts[:6]):
        record_review(store, a["alert_id"], "confirmed" if i % 2 == 0 else "rejected", "reviewer")
    precision, n = store.reviewed_precision()
    assert n == min(6, len(alerts))
    drift = check_drift_and_trigger(store, cfg)  # precision 0.5 < 0.6 -> trigger
    assert drift is True
    assert any(t["kind"] == "drift" for t in store.pending_triggers())


def test_cli_entrypoints_exist():
    from sentinel_loop import cli

    parser = cli.build_parser()
    # all prompt-required commands are registered
    for cmd in ("ingest", "extract-metrics", "label-windows", "build-dataset",
                "train-detection", "train-embedding-head", "evaluate", "serve", "shadow-run"):
        assert any(cmd in a for a in parser.format_help().split())
