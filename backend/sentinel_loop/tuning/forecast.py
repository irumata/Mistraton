"""Issue-forecasting report: multi-step forecast per configured metric plus the
first predicted band breach ("issue forecast with lead time") per metric.

Reads the raw metric exports (same series the dataset stage uses), resamples
each onto its sampling grid and asks the detector for `steps` ahead.
"""

from __future__ import annotations

from pathlib import Path

from ..config import MetricsConfig
from ..dataset.assemble import load_metric_series, resample_series
from .strategies import BaseDetector


def forecast_report(data_dir: str | Path, config: MetricsConfig, detector: BaseDetector,
                    steps: int = 12) -> dict:
    data_dir = Path(data_dir)
    series = load_metric_series(data_dir)
    forecasts: dict[str, list[dict]] = {}
    issues: list[dict] = []
    for m in config.metrics:
        points = series.get(m.name)
        if not points:
            continue
        grid, values = resample_series(points, m.sampling_seconds)
        if not grid:
            continue
        pts, issue = detector.forecast(m.name, values, steps, m.sampling_seconds, last_ts=grid[-1])
        forecasts[m.name] = [p.model_dump(mode="json") for p in pts]
        if issue is not None:
            issues.append(issue.model_dump(mode="json"))
    return {"strategy": detector.name, "steps": steps, "forecasts": forecasts, "issues": issues}
