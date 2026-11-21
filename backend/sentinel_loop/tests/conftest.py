"""Shared fixtures: mocked Mistral transport + a synthetic data dir."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sentinel_loop.config import Settings, load_metrics_config, resolve_config
from sentinel_loop.examples.synthetic import generate_synthetic
from sentinel_loop.ingest.loader import ingest_directory
from sentinel_loop.mistral_client import MistralClients

EXTRACTION_JSON = {
    "incident_summary": "API latency incident with error-rate precursor.",
    "metric_definitions": [
        {
            "name": "api_latency_ms",
            "source": "prometheus:api_gateway",
            "aggregation": "1m_avg",
            "expected_normal_range": ["60", "100"],
            "observed_bad_range": ["800", "3000"],
            "lead_time_minutes": 25,
            "why_relevant": "latency ramps before incident start",
        },
        {
            "name": "error_rate",
            "source": "prometheus:api_gateway",
            "aggregation": "1m_avg",
            "expected_normal_range": ["0", "0.002"],
            "observed_bad_range": ["0.05", "0.3"],
            "lead_time_minutes": 25,
            "why_relevant": "error rate rises gradually before onset",
        },
    ],
    "precursor_signals": [
        {"metric": "error_rate", "pattern": "gradual rise 20-25min before", "lead_time_minutes": 25},
        {"metric": "api_latency_ms", "pattern": "ramp over lead window", "lead_time_minutes": 25},
    ],
    "timeline": [],
    "root_cause_candidates": ["connection pool exhaustion"],
    "prevention_actions": ["pool autoscaling"],
}


def make_mock_transport() -> callable:
    """Transport double: Large -> extraction JSON; Shieldstral pair -> YES;
    Shieldstral policy -> passes with PII redacted."""

    def transport(model: str, messages: list[dict], response_format=None) -> str:
        user = messages[-1]["content"]
        if "metric-extraction" in messages[0]["content"] or "metric-extraction" in user:
            payload = dict(EXTRACTION_JSON)
            m = re.search(r"incident_id: (\S+)", user)
            payload["incident_id"] = m.group(1) if m else "inc-unknown"
            return json.dumps(payload)
        if "policy judge" in messages[0]["content"].lower():
            return "YES"
        if "content policy" in messages[0]["content"].lower():
            redacted = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", "[EMAIL]", user)
            return json.dumps({"passes": True, "redacted": redacted})
        return "YES"

    return transport


@pytest.fixture
def mock_clients() -> MistralClients:
    return MistralClients(transport=make_mock_transport())


@pytest.fixture
def data_dir(tmp_path) -> Path:
    """Synthetic example ingested into a tmp data dir."""
    raw = tmp_path / "data" / "raw"
    generate_synthetic(raw)
    ingest_directory(raw, tmp_path / "data")
    return tmp_path / "data"


@pytest.fixture
def config(data_dir):
    cfg, _ = resolve_config(data_dir, None)
    return cfg


@pytest.fixture
def settings(data_dir, tmp_path) -> Settings:
    return Settings(
        data_dir=str(data_dir),
        db_path=str(data_dir / "labels.db"),
        artifacts_dir=str(data_dir / "artifacts"),
    )
