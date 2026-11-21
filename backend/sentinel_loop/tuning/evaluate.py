"""Evaluation on the held-out incident test set (never touched by the tuner).

Per incident and per window: precision/recall/F1 for `incident` and `precursor`
(and the other states), mean lead time achieved (minutes between first
detection and incident start), false positives per 1000 window-hours, and mean
absolute quantile loss on normal data.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from ..config import MetricsConfig
from ..dataset.assemble import load_splits, load_windows
from ..schemas import ClassMetrics, DetectionEvent, EvaluationReport, WindowSample, WindowState
from .strategies import BaseDetector


def _prf1(tp: int, fp: int, fn: int) -> ClassMetrics:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return ClassMetrics(precision=p, recall=r, f1=f1)


def evaluate_strategy(
    detector: BaseDetector,
    data_dir: str | Path,
    config: MetricsConfig,
    split: str = "test",
) -> EvaluationReport:
    data_dir = Path(data_dir)
    splits = load_splits(data_dir / "dataset" / "splits.json")
    ids = set(splits[split])
    windows = [s for s in load_windows(data_dir / "dataset" / "windows.jsonl") if s.incident_id in ids]

    # group windows per (incident, metric), ordered by time
    by_stream: dict[tuple[str, str], list[WindowSample]] = defaultdict(list)
    for s in windows:
        by_stream[(s.incident_id, s.metric)].append(s)

    per_class_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    window_hours = 0.0
    lead_times: list[float] = []
    incident_detected: set[str] = set()

    step = timedelta(seconds=config.window.step_seconds)
    for (incident_id, metric), samples in by_stream.items():
        samples.sort(key=lambda s: s.window_start)
        history: list[list] = []
        first_detection: dict[str, float] = {}
        for s in samples:
            event: DetectionEvent | None = detector.detect(
                metric, s.values, history, window_start=s.window_start, window_end=s.window_end
            )
            history.append(s.values)
            pred = event.window_state if event else WindowState.normal
            for state in WindowState:
                if state == s.label and pred == state:
                    per_class_counts[state.value]["tp"] += 1
                elif state == s.label and pred != state:
                    per_class_counts[state.value]["fn"] += 1
                elif state != s.label and pred == state:
                    per_class_counts[state.value]["fp"] += 1
            window_hours += config.window.length_seconds / 3600.0
            if event and event.window_state in (WindowState.precursor, WindowState.incident):
                inc_start = None
                for r in _records(data_dir):
                    if r.incident_id == incident_id:
                        inc_start = r.timestamp_start
                if inc_start is not None:
                    lead = (inc_start - s.window_start).total_seconds() / 60.0
                    key = incident_id
                    if key not in first_detection or lead < first_detection[key]:
                        first_detection[key] = max(0.0, lead)
                    if event.window_state == WindowState.incident:
                        incident_detected.add(incident_id)
        for iid, lead in first_detection.items():
            lead_times.append(lead)

    per_class = {
        state: _prf1(c["tp"], c["fp"], c["fn"]) for state, c in per_class_counts.items()
    }
    fp = per_class_counts["precursor"]["fp"] + per_class_counts["incident"]["fp"]
    fp_rate = fp / window_hours * 1000.0 if window_hours else 0.0

    normal_test = [s for s in windows if s.label == WindowState.normal]
    qloss = detector.quantile_loss(normal_test)

    return EvaluationReport(
        strategy=detector.name,
        per_class=per_class,
        mean_lead_time_minutes=(sum(lead_times) / len(lead_times)) if lead_times else 0.0,
        false_positives_per_1000_window_hours=fp_rate,
        mean_quantile_loss_normal=qloss,
        incidents_evaluated=sorted(incident_detected),
    )


def _records(data_dir: Path):
    from ..ingest.loader import load_records

    return load_records(data_dir / "records.jsonl")


def save_evaluation(report: EvaluationReport, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_evaluation(path: str | Path) -> EvaluationReport:
    return EvaluationReport.model_validate_json(Path(path).read_text(encoding="utf-8"))
