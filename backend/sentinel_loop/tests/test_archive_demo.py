"""Tests for input archives (prepare/apply) and the demo replay (chain on stored data)."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinel_loop.archive import apply_archive, list_archives, make_archive
from sentinel_loop.config import Settings, load_policy, resolve_config
from sentinel_loop.dataset.assemble import build_dataset
from sentinel_loop.examples.synthetic import generate_synthetic
from sentinel_loop.ingest.loader import ingest_directory, load_records
from sentinel_loop.labeling.store import LabelingStore
from sentinel_loop.mistral_client import MistralClients
from sentinel_loop.serve.chain import DetectionChain
from sentinel_loop.serve.demo import load_bundle_series, run_demo
from sentinel_loop.serve.watcher import create_app
from sentinel_loop.tests.conftest import make_mock_transport
from sentinel_loop.tuning.train import train


@pytest.fixture
def demo_env(tmp_path):
    """Single-incident synthetic data dir (inc-001 only, so the merged training
    series has no cross-bundle timestamp collisions), ingested + dataset built."""
    data_dir = tmp_path / "data"
    raw = data_dir / "raw"
    generate_synthetic(raw)
    shutil.rmtree(raw / "inc-002")
    ingest_directory(raw, data_dir)
    config, _ = resolve_config(data_dir, None)
    build_dataset(data_dir, config)
    settings = Settings(data_dir=str(data_dir), db_path=str(data_dir / "labels.db"),
                        artifacts_dir=str(data_dir / "artifacts"))
    return data_dir, config, settings


def _work_src(tmp_path: Path) -> Path:
    """A mini work/ folder: raw bundles + records.jsonl + README."""
    src = tmp_path / "work"
    generate_synthetic(src / "raw")
    (src / "README.md").write_text("# work\n")
    ingest_directory(src / "raw", src)
    (src / "labels.db").write_bytes(b"derived - must not be archived")
    return src


def test_archive_roundtrip(tmp_path):
    src = _work_src(tmp_path)
    data_dir = tmp_path / "data"
    manifest = make_archive(src, data_dir=data_dir)
    assert manifest["file_count"] >= 4
    assert not any(n.startswith(("labels.db", "archive/")) for n in manifest["files"])

    archives = list_archives(data_dir)
    assert len(archives) == 1 and archives[0]["valid"] and archives[0]["file_count"] == manifest["file_count"]

    res = apply_archive(manifest["archive_path"], data_dir)
    assert res["ingested"] is True and res["records"] == 2
    assert (data_dir / "raw" / "inc-001" / "record.json").is_file()
    assert not (data_dir / "labels.db").exists()  # derived files are not in the archive
    assert {r.incident_id for r in load_records(data_dir / "records.jsonl")} == {"inc-001", "inc-002"}


def test_archive_rejects_tampered_member(tmp_path):
    src = _work_src(tmp_path)
    manifest = make_archive(src, data_dir=tmp_path / "d1")
    p = Path(manifest["archive_path"])
    with zipfile.ZipFile(p) as zf:
        data = {n: zf.read(n) for n in zf.namelist()}
    target = next(n for n in data if n.endswith("record.json"))
    data[target] = data[target] + b"tampered"
    with zipfile.ZipFile(p, "w") as zf:
        for n, b in data.items():
            zf.writestr(n, b)
    with pytest.raises(ValueError, match="sha256"):
        apply_archive(p, tmp_path / "d2")


def test_archive_rejects_zip_slip(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "version": 1, "created_at": "x", "source": "evil",
            "file_count": 1, "total_bytes": 1, "files": {"../escape.txt": "0" * 64},
        }))
        zf.writestr("../escape.txt", "x")
    with pytest.raises(ValueError, match="zip-slip"):
        apply_archive(evil, tmp_path / "d3")
    assert not (tmp_path / "escape.txt").exists()


def _trained(data_dir, config, settings):
    return train(data_dir, config, strategy="forecasting", artifacts_dir=Path(settings.artifacts_dir))


def test_demo_replay_shadow_fires_alerts(demo_env, mock_clients):
    data_dir, config, settings = demo_env
    det = _trained(data_dir, config, settings)
    store = LabelingStore(settings.db_path)
    policy = load_policy(config.policies_dir, "alert_verification_policy.yaml")
    chain = DetectionChain(det, config, mock_clients, question=policy.get("pair_question", "q"))
    report = run_demo(data_dir, config, det, chain, store=store, shadow=True, max_windows_per_metric=60)
    t = report["totals"]
    assert t["windows_evaluated"] > 0
    assert t["detection_events"] > 0
    assert t["alerts_fired"] > 0          # Timestral raised alarms (shadow: logged, not verified)
    assert t["suppressed_by_shieldstral"] == 0  # gate skipped in shadow mode
    assert store.list_alerts(10)           # alerts persisted for review


def test_demo_shieldstral_gate_suppresses_false_positives(demo_env):
    data_dir, config, settings = demo_env
    det = _trained(data_dir, config, settings)
    store = LabelingStore(settings.db_path)
    # Shieldstral always answers NO -> every Timestral candidate is suppressed
    rejecting = MistralClients(transport=make_mock_transport("NO"))
    policy = load_policy(config.policies_dir, "alert_verification_policy.yaml")
    chain = DetectionChain(det, config, rejecting, question=policy.get("pair_question", "q"))
    report = run_demo(data_dir, config, det, chain, store=store, shadow=False, max_windows_per_metric=60)
    t = report["totals"]
    assert t["detection_events"] > 0
    # every candidate that passed Timestral's confidence gate was rejected by
    # Shieldstral and suppressed (the rest were dropped as low-confidence)
    gated = [c for inc in report["incidents"] for c in inc["chains"]
             if c["event"] and c["event"]["model_confidence"] >= 0.7]
    assert gated and all(c["suppressed"] for c in gated)
    assert t["suppressed_by_shieldstral"] == len(gated)  # all false positives prevented
    assert t["alerts_fired"] == 0


def test_demo_live_gate_fires_with_large_explanation(demo_env, mock_clients):
    data_dir, config, settings = demo_env
    det = _trained(data_dir, config, settings)
    store = LabelingStore(settings.db_path)
    policy = load_policy(config.policies_dir, "alert_verification_policy.yaml")
    chain = DetectionChain(det, config, mock_clients, question=policy.get("pair_question", "q"))
    report = run_demo(data_dir, config, det, chain, store=store, shadow=False, max_windows_per_metric=60)
    t = report["totals"]
    assert t["alerts_fired"] > 0 and t["suppressed_by_shieldstral"] == 0
    alerts = report["alerts"]
    assert all(a["verified"] is True for a in alerts)
    # stage 3 ran: Large explanation printed + stored on the alert
    assert all(a["explanation"] and "SENTINEL-LOOP ALERT" in a["explanation"] for a in alerts)
    stages = {s["stage"] for inc in report["incidents"] for c in inc["chains"] for s in c["stages"]}
    assert {"timstral", "shieldstral", "large"} <= stages


def test_demo_endpoint(demo_env):
    data_dir, config, settings = demo_env
    det = _trained(data_dir, config, settings)
    app = create_app(detector=det, config=config, store=LabelingStore(settings.db_path),
                     clients=None, settings=settings)
    client = TestClient(app)
    r = client.post("/sentinel/demo", json={"shadow": True, "max_windows": 40})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["windows_evaluated"] > 0
    assert body["demo"] is True
    # single-incident selection
    r1 = client.post("/sentinel/demo", json={"incident_id": "inc-001", "shadow": True, "max_windows": 40})
    assert r1.status_code == 200
    assert [i["incident_id"] for i in r1.json()["incidents"]] == ["inc-001"]
    # unknown incident -> 404
    r2 = client.post("/sentinel/demo", json={"incident_id": "nope", "shadow": True})
    assert r2.status_code == 404


def test_load_bundle_series_long_and_wide(tmp_path):
    bundle = tmp_path / "b" / "metrics"
    bundle.mkdir(parents=True)
    (bundle / "api_latency_ms.csv").write_text("timestamp,value\n2026-01-01T00:00:00Z,10\n2026-01-01T00:01:00Z,11\n")
    (bundle / "access.csv").write_text("timestamp,response_time_ms,error_5xx\n2026-01-01T00:00:00Z,100,0\n")
    series = load_bundle_series(tmp_path / "b")
    assert set(series) == {"api_latency_ms", "response_time_ms", "error_5xx"}
    assert [p.value for p in series["api_latency_ms"]] == [10.0, 11.0]
