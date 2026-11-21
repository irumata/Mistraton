from datetime import datetime, timedelta, timezone

from sentinel_loop.labeling.labeler import label_all_windows, zone_label
from sentinel_loop.labeling.pii import regex_pii_filter
from sentinel_loop.labeling.store import LabelingStore
from sentinel_loop.schemas import IncidentRecord, WindowState


def _rec(start_h=3, dur_min=30):
    s = datetime(2026, 1, 10, start_h, 0, tzinfo=timezone.utc)
    return IncidentRecord(
        incident_id="inc-t", source_type="mixed", timestamp_start=s,
        timestamp_end=s + timedelta(minutes=dur_min), raw_text="ticket text",
    )


def test_zone_label_rules():
    rec = _rec()
    lead = timedelta(minutes=20)
    deg = timedelta(minutes=30)
    s = rec.timestamp_start
    assert zone_label(s, s + timedelta(minutes=5), rec, lead, deg) == WindowState.incident
    assert zone_label(s - lead, s, rec, lead, deg) == WindowState.precursor
    assert zone_label(s - lead - timedelta(minutes=30), s - lead - timedelta(minutes=25), rec, lead, deg) == WindowState.normal
    assert zone_label(rec.timestamp_end, rec.timestamp_end + timedelta(minutes=5), rec, lead, deg) == WindowState.degraded
    # overlap with incident wins over precursor
    assert zone_label(s - timedelta(minutes=2), s + timedelta(minutes=3), rec, lead, deg) == WindowState.incident


def test_pii_regex():
    text = "contact oncall@example.com from 10.0.0.1 or +1 415 555 1234"
    out = regex_pii_filter(text)
    assert "oncall@example.com" not in out
    assert "10.0.0.1" not in out


def test_label_all_windows(data_dir, config, mock_clients, settings):
    windows = label_all_windows(data_dir, settings.db_path, config, mock_clients)
    assert windows
    labels = {w.label for w in windows}
    assert WindowState.incident in labels
    assert WindowState.precursor in labels
    assert WindowState.normal in labels
    # shieldstral pair mock says YES -> non-normal windows validated
    non_normal = [w for w in windows if w.label != WindowState.normal]
    assert all(w.validated for w in non_normal)
    # PII filtered before storage: mock redacts emails
    assert all("@example.com" not in w.evidence for w in windows)

    store = LabelingStore(settings.db_path)
    stored = store.get_windows(metric="api_latency_ms", incident_id="inc-001")
    assert stored and all(w.incident_id == "inc-001" for w in stored)
    assert store.policy_version("labeling")


def test_label_offline_fallback(data_dir, config, settings):
    # no clients -> regex fallback, validated=False, still labels
    windows = label_all_windows(data_dir, settings.db_path, config, clients=None)
    non_normal = [w for w in windows if w.label != WindowState.normal]
    assert non_normal and not any(w.validated for w in non_normal)
    assert all("@example.com" not in w.evidence for w in windows)
