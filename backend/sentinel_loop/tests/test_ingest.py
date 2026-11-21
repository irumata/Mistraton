from sentinel_loop.ingest.loader import ingest_directory, load_records


def test_ingest_synthetic_bundles(data_dir):
    records = load_records(data_dir / "records.jsonl")
    assert {r.incident_id for r in records} == {"inc-001", "inc-002"}
    r1 = next(r for r in records if r.incident_id == "inc-001")
    assert r1.timestamp_start.year == 2026
    assert r1.timestamp_end > r1.timestamp_start
    assert "Postmortem" in r1.raw_text
    assert any("api_latency_ms" in s for s in r1.metric_sources)
    assert any("error_rate" in s for s in r1.metric_sources)
    assert r1.severity_hint == "SEV2"


def test_ingest_is_idempotent(tmp_path):
    from sentinel_loop.examples.synthetic import generate_synthetic

    raw = tmp_path / "raw"
    generate_synthetic(raw)
    first = ingest_directory(raw, tmp_path / "out")
    second = ingest_directory(raw, tmp_path / "out")
    assert [r.incident_id for r in first] == [r.incident_id for r in second]
