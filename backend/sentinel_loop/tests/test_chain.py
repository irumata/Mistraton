"""Unit tests for the live detection chain: timstral -> shieldstral -> large."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentinel_loop.config import MetricSpec, MetricsConfig
from sentinel_loop.mistral_client import MistralClients
from sentinel_loop.schemas import DetectionEvent, WindowState
from sentinel_loop.serve.chain import DetectionChain
from .conftest import make_mock_transport

WS = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
WE = WS + timedelta(minutes=5)


def make_config() -> MetricsConfig:
    return MetricsConfig(metrics=[MetricSpec(
        name="api_latency_ms", source="prometheus:api_gateway", sampling_seconds=60,
        expected_normal_range=["40", "120"], observed_bad_range=["800", "12000"],
        lead_time_minutes=18,
    )])


class StubDetector:
    """Duck-typed detector: returns a fixed event (or None)."""

    def __init__(self, event):
        self.event = event

    def detect(self, metric, window, history_windows, window_start=None, window_end=None):
        return self.event


def make_event(confidence=0.9, state=WindowState.incident) -> DetectionEvent:
    return DetectionEvent(
        metric="api_latency_ms", model_confidence=confidence, window_state=state,
        lead_time_minutes=18, window_start=WS, window_end=WE, strategy="forecasting",
    )


def run_chain(detector, clients, shadow=False, config=None) -> object:
    cfg = config or make_config()
    chain = DetectionChain(detector, cfg, clients)
    return chain.run("api_latency_ms", [1.0, 2.0, 3.0], [[0.5] * 5], WS, WE, shadow=shadow)


def test_timstral_no_anomaly_stops_chain():
    res = run_chain(StubDetector(None), MistralClients(transport=make_mock_transport()))
    assert res.fired is False and res.suppressed is False
    assert [s.stage for s in res.stages] == ["timstral"]
    assert res.stages[0].passed is False


def test_low_confidence_not_chained():
    res = run_chain(StubDetector(make_event(confidence=0.1)), MistralClients(transport=make_mock_transport()))
    assert res.fired is False
    assert [s.stage for s in res.stages] == ["timstral"]


def test_full_chain_timstral_shieldstral_large(capsys):
    clients = MistralClients(transport=make_mock_transport(shieldstral_verdict="YES"))
    res = run_chain(StubDetector(make_event()), clients)
    assert res.fired is True and res.suppressed is False
    assert [s.stage for s in res.stages] == ["timstral", "shieldstral", "large"]
    assert all(s.passed for s in res.stages)
    assert res.alert is not None and res.alert.verified is True
    assert res.explanation is not None and res.explanation.summary
    assert "SENTINEL-LOOP ALERT" in res.alert.explanation
    printed = capsys.readouterr().out
    assert "SENTINEL-LOOP ALERT" in printed
    assert "LIKELY CAUSES" in printed and "RECOMMENDED ACTIONS" in printed


def test_shieldstral_rejection_suppresses_false_positive(capsys):
    clients = MistralClients(transport=make_mock_transport(shieldstral_verdict="NO"))
    res = run_chain(StubDetector(make_event()), clients)
    assert res.fired is False and res.suppressed is True
    assert res.alert is None and res.explanation is None
    assert [s.stage for s in res.stages] == ["timstral", "shieldstral"]
    assert res.stages[1].passed is False
    assert capsys.readouterr().out == "", "nothing printed for a suppressed candidate"


def test_no_clients_fails_closed():
    res = run_chain(StubDetector(make_event()), clients=None)
    assert res.fired is False and res.suppressed is True
    assert [s.stage for s in res.stages] == ["timstral", "shieldstral"]


def test_shadow_mode_skips_shieldstral_but_explains():
    clients = MistralClients(transport=make_mock_transport())
    res = run_chain(StubDetector(make_event()), clients, shadow=True)
    assert res.fired is True
    assert res.alert.verified is None  # shadow: logged, not sent
    assert [s.stage for s in res.stages] == ["timstral", "large"]
    assert res.explanation is not None


def test_chain_config_can_disable_stages():
    cfg = make_config()
    cfg.chain.verify_with_shieldstral = False
    cfg.chain.explain_with_large = False
    res = run_chain(StubDetector(make_event()), clients=None, config=cfg)
    assert res.fired is True  # no verification required, no clients needed
    assert [s.stage for s in res.stages] == ["timstral"]
    assert res.explanation is None
