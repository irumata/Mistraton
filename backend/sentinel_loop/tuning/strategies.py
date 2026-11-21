"""Detection strategies behind a shared interface.

Each strategy can be fitted on the training split and then scores windows:
  detect(metric, window_values, history_windows) -> DetectionEvent | None
Detection semantics: the first deviating windows read as `precursor`; once the
deviation persists for `persistence_windows` windows it escalates to `incident`.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import MetricsConfig
from ..schemas import DetectionEvent, WindowSample, WindowState
from ..timestral import LocalTimestralBackend, window_features

STRATEGIES = ("forecasting", "embedding", "zero-shot")


def _last(values: list[Optional[float]]) -> Optional[float]:
    for v in reversed(values):
        if v is not None:
            return v
    return None


class BaseDetector:
    name = "base"

    def __init__(self, config: MetricsConfig, backend: LocalTimestralBackend | None = None):
        self.config = config
        self.backend = backend or LocalTimestralBackend(
            horizon=max(1, config.window.length_seconds // max(1, min(
                (m.sampling_seconds for m in config.metrics), default=60))),
            band_k=config.thresholds.band_k,
        )

    def _confidence(self, residual: float, scale: float) -> float:
        if scale <= 0:
            return 0.0
        return float(min(1.0, abs(residual) / (2 * scale)))

    def _state(self, trailing_deviations: int) -> WindowState:
        if trailing_deviations >= max(1, self.config.thresholds.persistence_windows):
            return WindowState.incident
        return WindowState.precursor

    def detect(self, metric: str, window: list[Optional[float]],
               history_windows: list[list[Optional[float]]], window_start=None, window_end=None,
               ) -> Optional[DetectionEvent]:
        raise NotImplementedError

    def quantile_loss(self, samples: list[WindowSample]) -> float:
        return 0.0

    # serialization ---------------------------------------------------------
    def to_dict(self) -> dict:
        return {"name": self.name, "config": json.loads(self.config.model_dump_json()),
                "backend": {"forecast": self.backend.forecast, "zeroshot": self.backend.zeroshot,
                            "horizon": self.backend.horizon, "band_k": self.backend.band_k}}

    @classmethod
    def from_dict(cls, data: dict) -> "BaseDetector":
        from ..config import MetricsConfig as MC

        config = MC.model_validate(data["config"])
        backend = LocalTimestralBackend(horizon=data["backend"]["horizon"], band_k=data["backend"]["band_k"])
        backend.forecast = data["backend"]["forecast"]
        backend.zeroshot = data["backend"]["zeroshot"]
        if data["name"] == "forecasting":
            det = ForecastingDetector(config, backend)
        elif data["name"] == "embedding":
            det = EmbeddingHeadDetector(config, backend)
            det.weights = np.array(data["weights"])
            det.bias = np.array(data["bias"])
            det.feat_mean = np.array(data["feat_mean"])
            det.feat_std = np.array(data["feat_std"])
        else:
            det = ZeroShotDetector(config, backend)
        return det


class ForecastingDetector(BaseDetector):
    """Primary: Timestral fine-tuned to forecast each stream on normal data;
    detection = observed outside the predicted quantile band, sustained."""

    name = "forecasting"

    def fit(self, series: dict, normal_mask: dict) -> None:
        self.backend.fit_forecast(series, normal_mask)

    def detect(self, metric, window, history_windows, window_start=None, window_end=None):
        hist = [v for hw in history_windows for v in hw]
        pred, lo, hi = self.backend.predict_band(metric, hist)
        last = _last(window)
        if last is None:
            return None
        residual = last - pred
        if not (residual < lo - pred or residual > hi - pred):
            return None
        flags = []
        k = max(1, self.config.thresholds.persistence_windows - 1)
        flat: list[Optional[float]] = []
        for hw in history_windows:
            p2, lo2, hi2 = self.backend.predict_band(metric, flat)
            lv = _last(hw)
            flags.append(lv is not None and (lv - p2 < lo2 - p2 or lv - p2 > hi2 - p2))
            flat.extend(hw)
        flags = flags[-k:]
        flags.append(True)
        trailing = 1
        for f in reversed(flags[:-1]):
            if f:
                trailing += 1
            else:
                break
        scale = max(hi - lo, 1e-9) / (2 * max(self.config.thresholds.band_k, 1e-9))
        conf = self._confidence(residual, max(scale, (hi - lo) / 2))
        if window_start is None:
            return None
        return DetectionEvent(
            metric=metric, model_confidence=conf, window_state=self._state(trailing),
            lead_time_minutes=self.config.lead_time_minutes(metric),
            window_start=window_start, window_end=window_end or window_start, strategy=self.name,
        )

    def quantile_loss(self, samples: list[WindowSample]) -> float:
        """Mean pinball (quantile) loss of observed vs predicted band on normal windows."""
        losses = []
        for s in samples:
            if s.label != WindowState.normal:
                continue
            hist: list[Optional[float]] = []
            pred, lo, hi = self.backend.predict_band(s.metric, hist)
            v = _last(s.values)
            if v is None:
                continue
            for tau, q in ((0.05, lo), (0.95, hi)):
                diff = v - q
                losses.append(max(tau * diff, (tau - 1) * diff))
        return float(np.mean(losses)) if losses else 0.0

    def to_dict(self) -> dict:
        d = super().to_dict()
        return d


class ZeroShotDetector(BaseDetector):
    """Control: untrained Timestral quantile-residual anomaly detection."""

    name = "zero-shot"

    def fit(self, series: dict, normal_mask: dict) -> None:
        self.backend.fit_zeroshot(series)

    def detect(self, metric, window, history_windows, window_start=None, window_end=None):
        score = self.backend.residual_score(metric, window)
        if score <= 0:
            return None
        flags = [self.backend.residual_score(metric, hw) > 0 for hw in history_windows]
        flags.append(True)
        trailing = 1
        for f in reversed(flags[:-1]):
            if f:
                trailing += 1
            else:
                break
        zs = self.backend.zeroshot.get(metric, {"q95": 1.0, "q05": 0.0})
        width = max(zs["q95"] - zs["q05"], 1e-9)
        if window_start is None:
            return None
        return DetectionEvent(
            metric=metric, model_confidence=min(1.0, score), window_state=self._state(trailing),
            lead_time_minutes=self.config.lead_time_minutes(metric),
            window_start=window_start, window_end=window_end or window_start, strategy=self.name,
        )

    def quantile_loss(self, samples: list[WindowSample]) -> float:
        losses = []
        for s in samples:
            if s.label != WindowState.normal:
                continue
            zs = self.backend.zeroshot.get(s.metric)
            v = _last(s.values)
            if zs is None or v is None:
                continue
            for tau, q in ((0.05, zs["q05"]), (0.95, zs["q95"])):
                diff = v - q
                losses.append(max(tau * diff, (tau - 1) * diff))
        return float(np.mean(losses)) if losses else 0.0


class EmbeddingHeadDetector(BaseDetector):
    """Timestral context representations of windows + lightweight softmax head."""

    name = "embedding"
    CLASSES = [WindowState.normal, WindowState.precursor, WindowState.incident, WindowState.degraded]

    def __init__(self, config, backend=None):
        super().__init__(config, backend)
        self.weights: Optional[np.ndarray] = None  # (n_features, n_classes)
        self.bias: Optional[np.ndarray] = None
        self.feat_mean = np.zeros(6)
        self.feat_std = np.ones(6)

    def fit(self, samples: list[WindowSample], epochs: int = 300, lr: float = 0.1) -> None:
        feats = np.array([window_features(s.values) for s in samples])
        self.feat_mean = feats.mean(axis=0)
        self.feat_std = feats.std(axis=0) + 1e-9
        X = (feats - self.feat_mean) / self.feat_std
        y_idx = np.array([self.CLASSES.index(s.label) for s in samples])
        n_features, n_classes = X.shape[1], len(self.CLASSES)
        self.weights = np.zeros((n_features, n_classes))
        self.bias = np.zeros(n_classes)
        Y = np.eye(n_classes)[y_idx]
        for _ in range(epochs):
            logits = X @ self.weights + self.bias
            logits -= logits.max(axis=1, keepdims=True)
            exp = np.exp(logits)
            probs = exp / exp.sum(axis=1, keepdims=True)
            grad = (probs - Y) / X.shape[0]
            self.weights -= lr * (X.T @ grad)
            self.bias -= lr * grad.sum(axis=0)

    def detect(self, metric, window, history_windows, window_start=None, window_end=None):
        if self.weights is None or window_start is None:
            return None
        x = (np.array(window_features(window)) - self.feat_mean) / self.feat_std
        logits = x @ self.weights + self.bias
        logits -= logits.max()
        exp = np.exp(logits)
        probs = exp / exp.sum()
        cls = int(np.argmax(probs))
        state = self.CLASSES[cls]
        if state == WindowState.normal:
            return None
        return DetectionEvent(
            metric=metric, model_confidence=float(probs[cls]), window_state=state,
            lead_time_minutes=self.config.lead_time_minutes(metric),
            window_start=window_start, window_end=window_end or window_start, strategy=self.name,
        )

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "weights": self.weights.tolist() if self.weights is not None else None,
            "bias": self.bias.tolist() if self.bias is not None else None,
            "feat_mean": self.feat_mean.tolist(), "feat_std": self.feat_std.tolist(),
        })
        return d


def save_detector(detector: BaseDetector, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = detector.to_dict()
    if detector.name == "embedding" and detector.weights is not None:
        payload["weights"] = detector.weights.tolist()
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def load_detector(path: str | Path) -> BaseDetector:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return BaseDetector.from_dict(data)
