"""Feedback loop + retraining triggers.

- Verified alerts and reviewer confirmations/rejections flow back into the
  labeling store (record_review).
- Retraining triggers: scheduled, on new incident data, and on drift
  (precision on reviewed alerts drops below threshold).
"""

from __future__ import annotations

from ..config import MetricsConfig
from ..labeling.store import LabelingStore
from ..schemas import AlertEvent


def record_review(store: LabelingStore, alert_id: str, decision: str, reviewer: str) -> AlertEvent | None:
    if decision not in ("confirmed", "rejected"):
        raise ValueError("decision must be 'confirmed' or 'rejected'")
    return store.record_review(alert_id, decision, reviewer)


def check_drift_and_trigger(store: LabelingStore, config: MetricsConfig) -> bool:
    """Drift trigger: precision on reviewed alerts below threshold -> retrain."""
    precision, n = store.reviewed_precision()
    if n >= config.thresholds.min_reviews_for_drift and precision < config.thresholds.drift_precision_threshold:
        store.add_trigger("drift", f"reviewed precision {precision:.2f} < {config.thresholds.drift_precision_threshold}")
        return True
    return False


def trigger_new_incident(store: LabelingStore, incident_id: str) -> None:
    store.add_trigger("new_incident", incident_id)


def trigger_scheduled(store: LabelingStore, detail: str = "scheduled") -> None:
    store.add_trigger("scheduled", detail)
