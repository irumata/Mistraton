"""Pydantic schemas for every stage boundary in Sentinel-Loop.

All cross-stage data is validated through these models. Timestamps are
timezone-aware UTC datetimes; JSON serialization goes through pydantic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceType(str, Enum):
    log = "log"
    metric_export = "metric_export"
    ticket = "ticket"
    postmortem = "postmortem"
    chat = "chat"
    mixed = "mixed"


class IncidentRecord(BaseModel):
    """Common normalized schema (JSONL) every ingested source becomes."""

    incident_id: str
    source_type: SourceType = SourceType.mixed
    timestamp_start: datetime
    timestamp_end: datetime
    raw_text: str = ""
    metric_sources: list[str] = Field(default_factory=list)
    severity_hint: Optional[str] = None
    linked_records: list[str] = Field(default_factory=list)


class MetricDefinition(BaseModel):
    """One entry of the monitoring contract (metrics.yaml)."""

    name: str
    source: str
    aggregation: str = "1m_avg"
    expected_normal_range: list[str] = Field(min_length=2, max_length=2)
    observed_bad_range: list[str] = Field(min_length=2, max_length=2)
    lead_time_minutes: float = 0.0
    why_relevant: str = ""


class PrecursorSignal(BaseModel):
    metric: str
    pattern: str
    lead_time_minutes: float


class TimelineEntry(BaseModel):
    timestamp: datetime
    description: str


class ExtractionOutput(BaseModel):
    """Structured JSON that Mistral Large emits for one incident bundle."""

    incident_id: str
    incident_summary: str
    metric_definitions: list[MetricDefinition] = Field(default_factory=list)
    precursor_signals: list[PrecursorSignal] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    root_cause_candidates: list[str] = Field(default_factory=list)
    prevention_actions: list[str] = Field(default_factory=list)


class WindowState(str, Enum):
    normal = "normal"
    precursor = "precursor"
    incident = "incident"
    degraded = "degraded"


# Severity ordering used when a window overlaps several zones.
STATE_SEVERITY = {
    WindowState.incident: 3,
    WindowState.precursor: 2,
    WindowState.degraded: 1,
    WindowState.normal: 0,
}


class LabelledWindow(BaseModel):
    metric: str
    incident_id: str
    window_start: datetime
    window_end: datetime
    label: WindowState
    evidence: str = ""
    policy_version: str = ""
    validated: bool = False


class MetricPoint(BaseModel):
    ts: datetime
    value: Optional[float] = None  # None = missing (Timestral handles natively)


class WindowSample(BaseModel):
    """One aligned window of one metric stream, with its label (detection set)
    or normal-only membership (forecasting set)."""

    incident_id: str
    metric: str
    window_start: datetime
    window_end: datetime
    values: list[Optional[float]]
    label: WindowState = WindowState.normal


class DetectionEvent(BaseModel):
    """What self-hosted Timestral emits on a live stream (serve /watch)."""

    metric: str
    model_confidence: float = Field(ge=0.0, le=1.0)
    window_state: WindowState
    lead_time_minutes: float
    window_start: datetime
    window_end: datetime
    strategy: str = ""


class AlertExplanation(BaseModel):
    """Mistral Large explanation of a fired alert (printed + stored on the alert).

    Produced by the third stage of the live detection chain, after Timestral
    flagged the window and Shieldstral verified it.
    """

    summary: str
    why_timestral_flagged: str = ""
    why_shieldstral_verified: str = ""
    likely_causes: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    raw: str = ""  # raw model output, kept for audit


class ChainStage(BaseModel):
    """One stage of the live detection chain (timstral -> shieldstral -> large)."""

    stage: str  # "timstral" | "shieldstral" | "large"
    model: str
    passed: bool
    detail: str = ""
    seconds: float = 0.0


class ChainResult(BaseModel):
    """Outcome of running one window through the detection chain."""

    event: Optional[DetectionEvent] = None
    alert: Optional[AlertEvent] = None
    explanation: Optional[AlertExplanation] = None
    stages: list[ChainStage] = Field(default_factory=list)
    fired: bool = False  # an alert was emitted (verified, or shadow-logged)
    suppressed: bool = False  # Shieldstral rejected the candidate (false positive avoided)


class AlertEvent(BaseModel):
    alert_id: str
    event: DetectionEvent
    verified: Optional[bool] = None  # None = shadow mode (logged, not sent)
    explanation: Optional[str] = None  # Mistral Large explanation, printed at fire time
    reviewer: str = ""
    decided_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class ForecastPoint(BaseModel):
    """One step of a multi-step forecast for a metric stream."""

    step: int  # 1-based horizon step
    predicted: float
    lower: float
    upper: float
    ts: Optional[datetime] = None  # expected timestamp of this step, if known


class IssueForecast(BaseModel):
    """First forecast step expected to breach the current normal band = the
    predicted issue, with lead time."""

    metric: str
    strategy: str = ""
    steps_ahead: int
    expected_in_minutes: float
    expected_breach_ts: Optional[datetime] = None
    predicted_value: float
    lower: float
    upper: float
    model_confidence: float = Field(ge=0.0, le=1.0)


class ClassMetrics(BaseModel):
    precision: float
    recall: float
    f1: float


class EvaluationReport(BaseModel):
    strategy: str
    per_class: dict[str, ClassMetrics]
    mean_lead_time_minutes: float
    false_positives_per_1000_window_hours: float
    mean_quantile_loss_normal: float
    incidents_evaluated: list[str] = Field(default_factory=list)
