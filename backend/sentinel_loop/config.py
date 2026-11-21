"""Versioned config loading for Sentinel-Loop.

`metrics.yaml`, policies and all thresholds live in versioned config files,
never hardcoded. This module loads them; the packaged `metrics.yaml` is the
outline/default, and `extract-metrics` writes the merged monitoring contract
into `<data-dir>/metrics.yaml`, which later stages prefer when present.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "metrics.yaml"
DEFAULT_POLICIES_DIR = PACKAGE_DIR / "policies"


class WindowConfig(BaseModel):
    length_seconds: int = 300
    step_seconds: int = 60
    degraded_minutes: float = 30.0
    default_lead_time_minutes: float = 20.0


class MetricSpec(BaseModel):
    name: str
    source: str = ""
    sampling_seconds: int = 60
    aggregation: str = "1m_avg"
    expected_normal_range: list[str] = Field(default_factory=lambda: ["0", "1"])
    observed_bad_range: list[str] = Field(default_factory=lambda: ["0", "1"])
    lead_time_minutes: float = 0.0
    why_relevant: str = ""


class Thresholds(BaseModel):
    band_k: float = 3.0  # quantile-band multiplier on forecast residual sigma
    persistence_windows: int = 3  # sustained deviation windows before "incident"
    alert_confidence: float = 0.7
    shadow_precision_threshold: float = 0.8  # precision needed to leave shadow mode
    drift_precision_threshold: float = 0.6  # below this on reviewed alerts -> retrain
    min_reviews_for_drift: int = 5


class TimestralConfig(BaseModel):
    size: str = "5M"  # 5M | 37M | 72M | 209M — start smallest, scale only on held-out eval
    backend: str = "local"  # local | forge | hf


class ChainConfig(BaseModel):
    """Live detection chain settings (serve /watch).

    Chain order is fixed: Timestral detects -> Shieldstral verifies (false-
    positive gate) -> Mistral Large explains (printed). Never reordered.
    """

    verify_with_shieldstral: bool = True  # gate candidate events through Shieldstral
    suppress_unverified: bool = True  # Shieldstral-rejected candidates are dropped, not fired
    explain_with_large: bool = True  # run the Large explanation stage on fired alerts
    explanation_max_chars: int = 4000  # cap for the prompt payload sent to Large


class MetricsConfig(BaseModel):
    """The monitoring contract + thresholds (metrics.yaml)."""

    version: int = 1
    window: WindowConfig = WindowConfig()
    metrics: list[MetricSpec] = Field(default_factory=list)
    thresholds: Thresholds = Thresholds()
    timestral: TimestralConfig = TimestralConfig()
    chain: ChainConfig = ChainConfig()
    policies_dir: str = str(DEFAULT_POLICIES_DIR)

    def metric(self, name: str) -> Optional[MetricSpec]:
        for m in self.metrics:
            if m.name == name:
                return m
        return None

    def lead_time_minutes(self, metric: str) -> float:
        m = self.metric(metric)
        if m and m.lead_time_minutes > 0:
            return m.lead_time_minutes
        return self.window.default_lead_time_minutes


class Settings(BaseModel):
    """Runtime settings from the environment."""

    mistral_api_key: str = Field(default_factory=lambda: os.environ.get("MISTRAL_API_KEY", ""))
    large_model: str = Field(default_factory=lambda: os.environ.get("SENTINEL_LARGE_MODEL", "mistral-large-latest"))
    shieldstral_model: str = Field(default_factory=lambda: os.environ.get("SENTINEL_SHIELDSTRAL_MODEL", "shieldstral-latest"))
    data_dir: str = Field(default_factory=lambda: os.environ.get("SENTINEL_DATA_DIR", "data"))
    db_path: str = Field(default_factory=lambda: os.environ.get("SENTINEL_DB", ""))  # empty -> <data_dir>/labels.db
    artifacts_dir: str = Field(default_factory=lambda: os.environ.get("SENTINEL_ARTIFACTS_DIR", ""))  # empty -> <data_dir>/artifacts
    cost_log_path: str = Field(default_factory=lambda: os.environ.get("SENTINEL_COST_LOG", ""))  # empty -> <data_dir>/cost_log.jsonl


def load_metrics_config(path: str | os.PathLike) -> MetricsConfig:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return MetricsConfig.model_validate(raw)


def resolve_config(data_dir: str | os.PathLike, config_arg: Optional[str]) -> tuple[MetricsConfig, Path]:
    """Prefer the merged <data-dir>/metrics.yaml produced by extract-metrics."""
    if config_arg:
        path = Path(config_arg)
    else:
        candidate = Path(data_dir) / "metrics.yaml"
        path = candidate if candidate.exists() else DEFAULT_CONFIG_PATH
    return load_metrics_config(path), path


def load_policy(policies_dir: str | os.PathLike, name: str) -> dict:
    path = Path(policies_dir) / name
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def db_path_for(settings: Settings) -> str:
    return settings.db_path or str(Path(settings.data_dir) / "labels.db")


def artifacts_dir_for(settings: Settings) -> str:
    return settings.artifacts_dir or str(Path(settings.data_dir) / "artifacts")
