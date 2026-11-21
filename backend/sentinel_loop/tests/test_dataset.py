import json

from sentinel_loop.dataset.assemble import (build_dataset, load_metric_series, load_splits,
                                             load_windows, resample_series)
from sentinel_loop.schemas import WindowState


def test_metric_series_loaded(data_dir):
    series = load_metric_series(data_dir)
    assert set(series) == {"api_latency_ms", "error_rate"}
    assert len(series["api_latency_ms"]) == 361  # 6h of 1m samples


def test_resample_masks_missing():
    from datetime import datetime, timedelta, timezone

    from sentinel_loop.schemas import MetricPoint

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    pts = [MetricPoint(ts=t0 + timedelta(minutes=i), value=float(i)) for i in (0, 1, 3)]  # minute 2 missing
    grid, values = resample_series(pts, 60)
    assert len(grid) == 4
    assert values[2] is None
    assert values[0] == 0.0 and values[3] == 3.0


def test_build_dataset(data_dir, config):
    res = build_dataset(data_dir, config)
    assert res["n_detection"] > 0 and res["n_forecast"] > 0
    windows = load_windows(res["windows"])
    labels = {w.label for w in windows}
    assert {WindowState.normal, WindowState.precursor, WindowState.incident, WindowState.degraded} <= labels
    # forecasting set is normal-only
    forecast = load_windows(res["forecast"])
    assert all(w.label == WindowState.normal for w in forecast)
    # incident-level splits are disjoint and cover all incidents
    splits = load_splits(res["splits"])
    all_ids = splits["train"] + splits["val"] + splits["test"]
    assert sorted(all_ids) == ["inc-001", "inc-002"]
    assert set(splits["train"]).isdisjoint(splits["test"])
    assert splits["test"]  # held-out incident test set exists
    # window values are aligned to the metric grid
    w = next(w for w in windows if w.label == WindowState.incident)
    assert len(w.values) == config.window.length_seconds // 60
