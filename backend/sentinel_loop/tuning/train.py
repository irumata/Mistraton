"""Training entry points (Timestral is the only model that is tuned).

- train-detection       : forecasting-based detection (primary)
- train-embedding-head  : embedding-based classification head
- zero-shot             : control baseline, no tuning

Fitting uses ONLY the train split (incident-level); the val split is for
threshold checks; the held-out incident test set is never touched here.
"""

from __future__ import annotations

from pathlib import Path

from ..config import MetricsConfig
from ..dataset.assemble import load_metric_series, load_splits, load_windows, resample_series
from ..schemas import WindowSample, WindowState
from .strategies import (STRATEGIES, BaseDetector, EmbeddingHeadDetector, ForecastingDetector,
                         ZeroShotDetector, save_detector)


def _series_and_mask(data_dir: Path, config: MetricsConfig, incident_ids: list[str]):
    """Reconstruct aligned per-metric series restricted to given incidents, plus
    a normal mask (True where the point falls outside any incident/lead/degraded zone)."""
    from datetime import timedelta

    from ..ingest.loader import load_records

    records = {r.incident_id: r for r in load_records(data_dir / "records.jsonl")}
    raw = load_metric_series(data_dir)
    series: dict[str, list] = {}
    mask: dict[str, list[bool]] = {}
    for m in config.metrics:
        points = raw.get(m.name)
        if not points:
            continue
        grid, values = resample_series(points, m.sampling_seconds)
        series[m.name] = [(ts, v) for ts, v in zip(grid, values)]
        flags = []
        for ts in grid:
            normal = True
            for iid in incident_ids:
                r = records.get(iid)
                if not r:
                    continue
                from datetime import timedelta as td

                lead = td(minutes=config.lead_time_minutes(m.name))
                deg = td(minutes=config.window.degraded_minutes)
                if r.timestamp_start - lead <= ts < r.timestamp_end + deg:
                    normal = False
                    break
            flags.append(normal)
        mask[m.name] = flags
    # convert to MetricPoint-like objects expected by the backend
    from ..schemas import MetricPoint

    series = {k: [MetricPoint(ts=ts, value=v) for ts, v in pts] for k, pts in series.items()}
    return series, mask


def train(data_dir: str | Path, config: MetricsConfig, strategy: str = "forecasting",
          artifacts_dir: str | Path | None = None) -> BaseDetector:
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {STRATEGIES}")
    data_dir = Path(data_dir)
    artifacts_dir = Path(artifacts_dir or data_dir / "artifacts")
    splits = load_splits(data_dir / "dataset" / "splits.json")
    train_ids = splits["train"]

    series, mask = _series_and_mask(data_dir, config, train_ids)

    if strategy == "forecasting":
        det: BaseDetector = ForecastingDetector(config)
        det.fit(series, mask)
    elif strategy == "zero-shot":
        det = ZeroShotDetector(config)
        det.fit(series, mask)  # zero-shot uses unlabeled global quantiles
    else:
        det = EmbeddingHeadDetector(config)
        train_windows = [s for s in load_windows(data_dir / "dataset" / "windows.jsonl")
                         if s.incident_id in train_ids]
        det.fit(train_windows)

    path = save_detector(det, artifacts_dir / f"detector_{strategy}.json")
    det.artifact_path = str(path)  # type: ignore[attr-defined]
    return det
