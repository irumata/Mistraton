"""Timestral self-hosted backend interface + local reference implementation.

Only Timestral detects, and only Timestral is tuned. In production this is a
small self-hosted checkpoint (default 5M; 37M/72M/209M scale-up options) served
on CPU/minimal GPU via Mistral Forge fine-tuning or open-weights fine-tuning.

`TimestralBackend` is the protocol the tuning/serving stages talk to.
`LocalTimestralBackend` is a dependency-light numpy implementation of the same
interface (rolling-quantile forecaster + window-embedding features) so the full
pipeline runs and is testable without downloading weights. Swap in a real
checkpoint by implementing the same protocol — roles never change.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import numpy as np

from .schemas import MetricPoint, WindowSample


@runtime_checkable
class TimestralBackend(Protocol):
    def fit_forecast(self, series: dict[str, list[MetricPoint]], normal: dict[str, list[bool]]) -> None: ...
    def predict_band(self, metric: str, history: list[Optional[float]]) -> tuple[float, float, float]: ...
    def forecast_horizon(self, metric: str, history: list[Optional[float]], steps: int) -> list[tuple[float, float, float]]: ...
    def embed_window(self, values: list[Optional[float]]) -> list[float]: ...
    def residual_score(self, metric: str, values: list[Optional[float]]) -> float: ...


def _clean(values: list[Optional[float]]) -> np.ndarray:
    return np.array([v for v in values if v is not None], dtype=float)


def window_features(values: list[Optional[float]]) -> list[float]:
    """Embedding-style context representation of a window (normalized stats)."""
    arr = _clean(values)
    if arr.size == 0:
        return [0.0] * 6
    x = np.arange(arr.size, dtype=float)
    slope = float(np.polyfit(x, arr, 1)[0]) if arr.size > 1 else 0.0
    mean = float(arr.mean())
    return [
        mean,
        float(arr.std()),
        float(arr.min()),
        float(arr.max()),
        slope,
        float(arr[-1] / mean) if mean else 0.0,
    ]


class LocalTimestralBackend:
    """Reference self-hosted backend.

    - forecasting: per-metric prediction = mean of the previous `horizon`
      observed values; residual sigma / quantiles learned on normal data.
    - zero-shot: global per-metric value quantiles, no fitting.
    """

    def __init__(self, horizon: int = 5, band_k: float = 3.0):
        self.horizon = horizon
        self.band_k = band_k
        self.forecast: dict[str, dict] = {}
        self.zeroshot: dict[str, dict] = {}

    # -- fitting -----------------------------------------------------------
    def fit_forecast(self, series: dict[str, list[MetricPoint]], normal: dict[str, list[bool]]) -> None:
        for metric, points in series.items():
            values = [p.value for p in points]
            mask = normal.get(metric, [True] * len(values))
            vals = np.array([v for v, m in zip(values, mask) if v is not None and m], dtype=float)
            if vals.size < self.horizon + 1:
                vals = _clean(values)
            residuals = []
            for i in range(self.horizon, len(vals)):
                pred = float(vals[i - self.horizon:i].mean())
                residuals.append(float(vals[i]) - pred)
            if not residuals:
                residuals = [0.0]
            r = np.array(residuals)
            self.forecast[metric] = {
                "sigma": float(r.std()) or 1e-6,
                "q05": float(np.quantile(r, 0.05)),
                "q95": float(np.quantile(r, 0.95)),
            }

    def fit_zeroshot(self, series: dict[str, list[MetricPoint]]) -> None:
        for metric, points in series.items():
            vals = _clean([p.value for p in points])
            if vals.size == 0:
                vals = np.array([0.0])
            self.zeroshot[metric] = {
                "q05": float(np.quantile(vals, 0.05)),
                "q95": float(np.quantile(vals, 0.95)),
                "mean": float(vals.mean()),
                "sigma": float(vals.std()) or 1e-6,
            }

    # -- inference ----------------------------------------------------------
    def predict_band(self, metric: str, history: list[Optional[float]]) -> tuple[float, float, float]:
        """Returns (prediction, lower, upper) for the next step."""
        stats = self.forecast.get(metric)
        hist = _clean(history[-self.horizon:] if history else [])
        pred = float(hist.mean()) if hist.size else (stats or {}).get("mean", 0.0)
        if stats is None:
            zs = self.zeroshot.get(metric, {"mean": pred, "sigma": 1e-6})
            pred = zs["mean"]
            return pred, pred - self.band_k * zs["sigma"], pred + self.band_k * zs["sigma"]
        half = max(self.band_k * stats["sigma"], stats["q95"] - stats["q05"])
        return pred, pred - half, pred + half

    def forecast_horizon(self, metric: str, history: list[Optional[float]], steps: int) -> list[tuple[float, float, float]]:
        """Multi-step-ahead forecast: per step h in 1..steps a (predicted, lower, upper).

        Prediction = trailing-window mean + trailing slope * h; the band
        half-width max(band_k * sigma, q95 - q05) widens with sqrt(h). Residual
        stats come from the fitted forecast, falling back to zero-shot stats,
        then to the history itself.
        """
        hist = _clean(history)
        stats = self.forecast.get(metric) or self.zeroshot.get(metric)
        tail = hist[-self.horizon:] if hist.size >= self.horizon else hist
        if tail.size:
            base = float(tail.mean())
            slope = float(np.polyfit(np.arange(tail.size, dtype=float), tail, 1)[0]) if tail.size > 1 else 0.0
            sigma = float(tail.std()) or 1e-6
            q05 = float(np.quantile(tail, 0.05))
            q95 = float(np.quantile(tail, 0.95))
        else:
            base = (stats or {}).get("mean", 0.0)
            slope = 0.0
            sigma = 1e-6
            q05, q95 = base - sigma, base + sigma
        if stats is not None:
            sigma = float(stats.get("sigma", sigma))
            q05 = float(stats.get("q05", q05))
            q95 = float(stats.get("q95", q95))
        half0 = max(self.band_k * sigma, q95 - q05, 1e-9)
        out: list[tuple[float, float, float]] = []
        for h in range(1, max(1, steps) + 1):
            pred = base + slope * h
            half = half0 * (h ** 0.5)
            out.append((pred, pred - half, pred + half))
        return out

    def embed_window(self, values: list[Optional[float]]) -> list[float]:
        return window_features(values)

    def residual_score(self, metric: str, values: list[Optional[float]]) -> float:
        """Zero-shot anomaly score: 0 inside the global band, else distance outside."""
        zs = self.zeroshot.get(metric)
        if zs is None:
            return 0.0
        arr = _clean(values)
        if arr.size == 0:
            return 0.0
        v = float(arr[-1])
        width = max(zs["q95"] - zs["q05"], 1e-9)
        return max(zs["q05"] - v, v - zs["q95"], 0.0) / width

    # -- persistence helper ---------------------------------------------------
    @staticmethod
    def count_trailing_deviations(residual_flags: list[bool]) -> int:
        n = 0
        for flag in reversed(residual_flags):
            if flag:
                n += 1
            else:
                break
        return n


def build_series_from_samples(samples: list[WindowSample], sampling_seconds: int) -> dict[str, list[MetricPoint]]:
    """Reconstruct per-metric point series from window samples (dataset stage helper)."""
    series: dict[str, list[MetricPoint]] = {}
    for s in samples:
        points = series.setdefault(s.metric, [])
        step = sampling_seconds
        from datetime import timedelta

        for i, v in enumerate(s.values):
            points.append(MetricPoint(ts=s.window_start + timedelta(seconds=i * step), value=v))
    for pts in series.values():
        pts.sort(key=lambda p: p.ts)
    return series
