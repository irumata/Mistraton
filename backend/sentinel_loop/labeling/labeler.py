"""Window labeling with Shieldstral validation.

For each incident and metric, sliding windows are labeled by time rules:
  incident   : overlaps [start, end)
  precursor  : overlaps [start - lead_time, start)
  degraded   : overlaps [end, end + degraded_minutes)
  normal     : otherwise
(a window overlapping several zones takes the most severe label).

Each label is validated by Shieldstral pair mode against the incident's text
evidence (policy question from policies/labeling_policy.yaml), and all text
is PII-filtered (Shieldstral policy mode, regex fallback) before storage.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from ..config import MetricsConfig, load_policy
from ..ingest.loader import load_records
from ..mistral_client import MistralClients
from ..schemas import (STATE_SEVERITY, IncidentRecord, LabelledWindow, WindowState)
from .pii import regex_pii_filter
from .store import LabelingStore

logger = logging.getLogger(__name__)

LABEL_QUESTION = "Does this evidence describe the onset of the incident referenced by this window?"


def zone_label(window_start: datetime, window_end: datetime, incident: IncidentRecord,
               lead: timedelta, degraded: timedelta) -> WindowState:
    s, e = incident.timestamp_start, incident.timestamp_end
    zones = [
        (WindowState.incident, s, e),
        (WindowState.precursor, s - lead, s),
        (WindowState.degraded, e, e + degraded),
    ]
    best = WindowState.normal
    for state, zs, ze in zones:
        if window_start < ze and window_end > zs:  # overlap
            if STATE_SEVERITY[state] > STATE_SEVERITY[best]:
                best = state
    return best


def _filter_text(clients: MistralClients | None, text: str, pii_policy: str) -> str:
    if clients is not None:
        try:
            return clients.shieldstral_pii_filter(text, pii_policy)
        except RuntimeError:
            pass  # offline: regex fallback below
    return regex_pii_filter(text)


def _validate(clients: MistralClients | None, question: str, evidence: str, window_desc: str) -> bool:
    if clients is None:
        return False
    try:
        return clients.shieldstral_pair(question, evidence, window_desc)
    except RuntimeError:
        return False


def label_for_incident(
    incident: IncidentRecord,
    metric: str,
    config: MetricsConfig,
    store: LabelingStore,
    clients: MistralClients | None = None,
    policies_dir: str | Path | None = None,
) -> list[LabelledWindow]:
    policies_dir = Path(policies_dir or config.policies_dir)
    labeling_policy = load_policy(policies_dir, "labeling_policy.yaml")
    pii_policy = load_policy(policies_dir, "pii_policy.yaml")
    policy_version = store.policy_version("labeling") or labeling_policy.get("version", "1")
    store.save_policy_version(policy_version, "labeling", str(labeling_policy))
    store.save_policy_version(
        pii_policy.get("version", "1"), "pii", str(pii_policy)
    )

    question = labeling_policy.get("pair_question", LABEL_QUESTION)
    lead = timedelta(minutes=config.lead_time_minutes(metric))
    degraded = timedelta(minutes=config.window.degraded_minutes)
    wlen = timedelta(seconds=config.window.length_seconds)
    step = timedelta(seconds=config.window.step_seconds)

    evidence_full = _filter_text(clients, incident.raw_text[:4000], str(pii_policy.get("rules", "")))

    windows: list[LabelledWindow] = []
    # slide over the incident-relevant range (lead-in .. degraded end)
    t = incident.timestamp_start - lead - wlen
    end_limit = incident.timestamp_end + degraded
    while t + wlen <= end_limit + step:
        ws, we = t, t + wlen
        label = zone_label(ws, we, incident, lead, degraded)
        evidence = evidence_full if label != WindowState.normal else ""
        window_desc = (
            f"metric={metric} window=[{ws.isoformat()}, {we.isoformat()}) "
            f"incident=[{incident.timestamp_start.isoformat()}, {incident.timestamp_end.isoformat()}) "
            f"proposed_label={label.value}"
        )
        validated = _validate(clients, question, evidence or "(no text evidence)", window_desc) \
            if label != WindowState.normal else True
        windows.append(LabelledWindow(
            metric=metric, incident_id=incident.incident_id, window_start=ws, window_end=we,
            label=label, evidence=evidence if label != WindowState.normal else "",
            policy_version=policy_version, validated=validated,
        ))
        t += step
    store.save_windows(windows)
    return windows


def label_all_windows(
    data_dir: str | Path,
    db_path: str | Path,
    config: MetricsConfig,
    clients: MistralClients | None = None,
) -> list[LabelledWindow]:
    data_dir = Path(data_dir)
    records = load_records(data_dir / "records.jsonl")
    store = LabelingStore(db_path)
    all_windows: list[LabelledWindow] = []
    for record in records:
        for metric in config.metrics:
            all_windows.extend(
                label_for_incident(record, metric.name, config, store, clients, config.policies_dir)
            )
    logger.info("labeled %d windows across %d incidents", len(all_windows), len(records))
    return all_windows
