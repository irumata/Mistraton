import yaml

from sentinel_loop.extract_metrics.extractor import extract_all, load_extractions


def test_extract_all_writes_contract(data_dir, config, mock_clients):
    outputs = extract_all(data_dir, mock_clients, config)
    assert {o.incident_id for o in outputs} == {"inc-001", "inc-002"}
    for o in outputs:
        names = {m.name for m in o.metric_definitions}
        assert names == {"api_latency_ms", "error_rate"}
        assert all(m.lead_time_minutes == 25 for m in o.metric_definitions)

    merged = data_dir / "metrics.yaml"
    assert merged.exists()
    raw = yaml.safe_load(merged.read_text())
    assert {m["name"] for m in raw["metrics"]} == {"api_latency_ms", "error_rate"}
    assert raw["thresholds"]["band_k"] == 3.0

    loaded = load_extractions(data_dir)
    assert len(loaded) == 2
    assert all(o.precursor_signals for o in loaded)


def test_parse_extraction_rejects_bad_json(mock_clients):
    from sentinel_loop.extract_metrics.extractor import parse_extraction

    import pytest

    with pytest.raises(Exception):
        parse_extraction("inc-x", "not json")
