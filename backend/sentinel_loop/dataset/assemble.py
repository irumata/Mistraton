"""Dataset assembly.

- Load raw metric exports, resample each metric onto an equally spaced grid
  (its sampling_seconds), masking missing values as None (Timestral supports
  native missing-data handling).
- Slide windows per metric per incident and label them (reusing the labeling
  rules); write the detection set (all labels) and forecasting set (normal
  windows only) as JSONL.
- Fixed train/val/test splits at the INCIDENT level: all windows of one
  incident stay in one split; the held-out incident test set is never touched
  by the tuner.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import MetricsConfig
from ..ingest.loader import load_records
from ..labeling.labeler import zone_label
from ..schemas import IncidentRecord, MetricPoint, WindowSample, WindowState


def load_metric_series(data_dir: str | Path) -> dict[str, list[MetricPoint]]:
    """Load every metric CSV under <data-dir>/raw/*/metrics/*.csv (long or wide)."""
    data_dir = Path(data_dir)
    series: dict[str, list[MetricPoint]] = {}
    raw = data_dir / "raw"
    if not raw.is_dir():
        return series
    for csv_path in sorted(raw.rglob("*.csv")):
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            cols = [c.strip() for c in header[1:]]
            if len(cols) <= 1:
                name, idx = csv_path.stem, 1
                targets = [(name, idx)]
            else:
                targets = [(c, i + 1) for i, c in enumerate(cols)]
            for row in reader:
                if not row:
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
    for pts in series.values():
        pts.sort(key=lambda p: p.ts)
    return series


def resample_series(points: list[MetricPoint], sampling_seconds: int) -> tuple[list[datetime], list[float | None]]:
    """Equally spaced grid; bucket-average values; missing buckets -> None."""
    if not points:
        return [], []
    step = timedelta(seconds=sampling_seconds)
    t0 = points[0].ts.replace(second=0, microsecond=0)
    t1 = points[-1].ts
    grid: list[datetime] = []
    t = t0
    while t <= t1:
        grid.append(t)
        t += step
    sums = [0.0] * len(grid)
    cnts = [0] * len(grid)
    for p in points:
        if p.value is None:
            continue
        i = int((p.ts - t0).total_seconds() // sampling_seconds)
        if 0 <= i < len(grid):
            sums[i] += p.value
            cnts[i] += 1
    values: list[float | None] = [sums[i] / cnts[i] if cnts[i] else None for i in range(len(grid))]
    return grid, values


def _window_values(grid: list[datetime], values: list[float | None], start_idx: int, length: int) -> list[float | None]:
    return values[start_idx:start_idx + length]


def build_dataset(data_dir: str | Path, config: MetricsConfig, seed: int = 42,
                  ratios: tuple[float, float, float] = (0.7, 0.15, 0.15)) -> dict:
    """Build detection/forecasting sets + incident-level splits. Returns paths."""
    data_dir = Path(data_dir)
    records = load_records(data_dir / "records.jsonl")
    series = load_metric_series(data_dir)

    wlen_s = config.window.length_seconds
    step_s = config.window.step_seconds
    degraded = timedelta(minutes=config.window.degraded_minutes)

    detection: list[WindowSample] = []
    forecasting: list[WindowSample] = []
    for record in records:
        for metric in config.metrics:
            m = metric
            points = series.get(m.name)
            if not points:
                continue
            grid, values = resample_series(points, m.sampling_seconds)
            if not grid:
                continue
            length = max(1, wlen_s // m.sampling_seconds)
            step = max(1, step_s // m.sampling_seconds)
            lead = timedelta(minutes=config.lead_time_minutes(m.name))
            lo = record.timestamp_start - lead - timedelta(seconds=wlen_s)
            hi = record.timestamp_end + degraded
            start_idx = 0
            while grid[start_idx] < lo and start_idx < len(grid) - 1:
                start_idx += 1
            i = max(0, start_idx - length)
            while i + length <= len(grid) and grid[i] + timedelta(seconds=wlen_s) <= hi + timedelta(seconds=step_s):
                ws = grid[i]
                we = grid[i] + timedelta(seconds=wlen_s)
                label = zone_label(ws, we, record, lead, degraded)
                sample = WindowSample(
                    incident_id=record.incident_id, metric=m.name,
                    window_start=ws, window_end=we,
                    values=_window_values(grid, values, i, length), label=label,
                )
                detection.append(sample)
                if label == WindowState.normal:
                    forecasting.append(sample)
                i += step

    # incident-level splits (all windows of one incident stay in one split)
    incident_ids = sorted({r.incident_id for r in records})
    rng = random.Random(seed)
    rng.shuffle(incident_ids)
    n = len(incident_ids)
    n_test = max(1, round(n * ratios[2])) if n >= 2 else 0
    n_val = max(1, round(n * ratios[1])) if n >= 3 else 0
    test_ids = incident_ids[:n_test]
    val_ids = incident_ids[n_test:n_test + n_val]
    train_ids = incident_ids[n_test + n_val:]
    splits = {"train": train_ids, "val": val_ids, "test": test_ids, "seed": seed}

    out = data_dir / "dataset"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "windows.jsonl", "w", encoding="utf-8") as fh:
        for s in detection:
            fh.write(s.model_dump_json() + "\n")
    with open(out / "forecast_windows.jsonl", "w", encoding="utf-8") as fh:
        for s in forecasting:
            fh.write(s.model_dump_json() + "\n")
    (out / "splits.json").write_text(json.dumps(splits, indent=2), encoding="utf-8")
    return {"windows": out / "windows.jsonl", "forecast": out / "forecast_windows.jsonl",
            "splits": out / "splits.json", "n_detection": len(detection), "n_forecast": len(forecasting)}


def load_windows(path: str | Path) -> list[WindowSample]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(WindowSample.model_validate_json(line))
    return out


def load_splits(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
