"""Demo replay: run the fixed detection chain over a stored incident bundle.

`run_demo` loads one incident bundle's metric exports from
`<data-dir>/raw/<incident_id>/metrics/*.csv`, resamples each configured metric
onto its sampling grid, slides windows exactly like the live watcher, and runs
every window through the fixed chain (serve/chain.py):

    1. Timestral detects   -> candidate DetectionEvent
    2. Shieldstral verifies -> NO => suppressed (false positive avoided)
    3. Mistral Large explains the fired alert (printed + stored)

This is the "run it on this data" option: it shows how Timestral raises alarms
on real incident windows and how Shieldstral prevents the false alarms (e.g.
planned-deploy hard negatives) before Large explains the fired ones.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..config import MetricsConfig
from ..dataset.assemble import resample_series
from ..labeling.store import LabelingStore
from ..mistral_client import MistralClients
from ..schemas import AlertEvent, ChainResult, DetectionEvent, MetricPoint
from ..tuning.strategies import BaseDetector
from .chain import DetectionChain

logger = logging.getLogger(__name__)


def load_bundle_series(bundle_dir: str | Path) -> dict[str, list[MetricPoint]]:
    """Load one bundle's metric exports: `metrics/*.csv`, long (timestamp,value)
    or wide (timestamp,col1,col2,...). Wide columns are named by header."""
    bundle = Path(bundle_dir)
    series: dict[str, list[MetricPoint]] = {}
    mdir = bundle / "metrics"
    if not mdir.is_dir():
        return series
    for csv_path in sorted(mdir.glob("*.csv")):
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            cols = [c.strip() for c in header[1:]]
            if len(cols) <= 1:
                targets = [(csv_path.stem, 1)]
            else:
                targets = [(c, i + 1) for i, c in enumerate(cols)]
            for row in reader:
                if not row or not row[0].strip():
                    continue
                try:
                    ts = datetime.fromisoformat(row[0].strip().replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                for name, idx in targets:
                    if len(row) > idx and row[idx].strip() != "":
                        try:
                            series.setdefault(name, []).append(MetricPoint(ts=ts, value=float(row[idx])))
                        except ValueError:
                            series.setdefault(name, []).append(MetricPoint(ts=ts, value=None))
                    else:
                        series.setdefault(name, []).append(MetricPoint(ts=ts, value=None))
    for pts in series.values():
        pts.sort(key=lambda p: p.ts)
    return series


def _evenly_spaced(indices: list[int], k: int) -> list[int]:
    """Pick ~k evenly spaced positions from indices (always keeps first+last)."""
    if len(indices) <= k:
        return indices
    if k <= 1:
        return [indices[0]]
    return sorted({round(i * (len(indices) - 1) / (k - 1)) for i in range(k)})


def run_demo(
    data_dir: str | Path,
    config: MetricsConfig,
    detector: BaseDetector,
    chain: DetectionChain,
    store: Optional[LabelingStore] = None,
    incident_id: Optional[str] = None,
    metrics: Optional[list[str]] = None,
    shadow: bool = True,
    max_windows_per_metric: int = 80,
    window_scope: str = "incident",  # "incident" = around the incident window, "full" = whole series
) -> dict:
    """Replay the detection chain over stored incident bundle(s).

    With `incident_id=None` every bundle under <data-dir>/raw is replayed.
    `window_scope="incident"` evaluates windows from lead-in through the
    degraded tail (where alarms matter); `"full"` replays the whole series
    (catches hard negatives like planned deploys that must be suppressed).
    Returns a JSON-serializable report: per-incident window/chain/alert traces
    plus totals (events, fired alerts, suppressed false positives).
    """
    data_dir = Path(data_dir)
    raw = data_dir / "raw"
    if incident_id:
        bundles = [raw / incident_id] if (raw / incident_id).is_dir() else []
        if not bundles:
            raise FileNotFoundError(f"no raw bundle for incident {incident_id!r} under {raw}")
    else:
        bundles = sorted(p for p in raw.iterdir() if p.is_dir()) if raw.is_dir() else []

    records_path = data_dir / "records.jsonl"
    records = {}
    if records_path.exists():
        from ..ingest.loader import load_records

        records = {r.incident_id: r for r in load_records(records_path)}

    wlen = timedelta(seconds=config.window.length_seconds)
    step = timedelta(seconds=config.window.step_seconds)

    incidents: list[dict] = []
    tot_windows = tot_events = tot_fired = tot_suppressed = 0
    all_alerts: list[AlertEvent] = []

    for bundle in bundles:
        series = load_bundle_series(bundle)
        names = metrics or [m.name for m in config.metrics]
        inc_report = {"incident_id": bundle.name, "metrics": [], "windows": 0,
                      "events": 0, "fired": 0, "suppressed": 0,
                      "chains": [], "alerts": []}
        for name in names:
            spec = config.metric(name)
            if spec is None or name not in series:
                continue
            grid, values = resample_series(series[name], spec.sampling_seconds)
            n = max(1, config.window.length_seconds // spec.sampling_seconds)
            step_idx = max(1, config.window.step_seconds // spec.sampling_seconds)
            if len(grid) < n:
                continue
            history: list[list] = []
            evaluated = 0
            starts = list(range(0, len(grid) - n + 1, step_idx))
            rec = records.get(bundle.name)
            if window_scope == "incident" and rec is not None:
                lead = timedelta(minutes=config.lead_time_minutes(name))
                lo = rec.timestamp_start - lead - 2 * wlen
                hi = rec.timestamp_end + timedelta(minutes=config.window.degraded_minutes) + wlen
                starts = [s for s in starts if lo <= grid[s] and grid[s] + wlen <= hi + step]
            starts = [starts[i] for i in _evenly_spaced(list(range(len(starts))), max_windows_per_metric)]
            for s in starts:
                vals = values[s:s + n]
                ws = grid[s]
                we = ws + wlen
                res = chain.run(name, vals, history, window_start=ws, window_end=we, shadow=shadow)
                history.append(vals)
                evaluated += 1
                inc_report["windows"] += 1
                if res.event is not None:
                    inc_report["events"] += 1
                if res.suppressed:
                    inc_report["suppressed"] += 1
                if res.fired and res.alert is not None:
                    inc_report["fired"] += 1
                    if store is not None:
                        store.save_alert(res.alert)
                    inc_report["alerts"].append(res.alert)
                    all_alerts.append(res.alert)
                if res.event is not None:  # trace only windows that produced a candidate
                    inc_report["chains"].append(res)
            inc_report["metrics"].append(name)
        inc_report["chains"] = [c.model_dump(mode="json") for c in inc_report["chains"]]
        inc_report["alerts"] = [a.model_dump(mode="json") for a in inc_report["alerts"]]
        incidents.append(inc_report)
        tot_windows += inc_report["windows"]
        tot_events += inc_report["events"]
        tot_fired += inc_report["fired"]
        tot_suppressed += inc_report["suppressed"]

    return {
        "demo": True,
        "shadow": shadow,
        "window_scope": window_scope,
        "detector": detector.name,
        "incidents": incidents,
        "totals": {
            "incidents": len(incidents),
            "windows_evaluated": tot_windows,
            "detection_events": tot_events,
            "alerts_fired": tot_fired,
            "suppressed_by_shieldstral": tot_suppressed,
        },
        "alerts": [a.model_dump(mode="json") for a in all_alerts],
    }
