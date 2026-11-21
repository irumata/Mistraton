"""Tuning stage: only Timestral detects, only Timestral is tuned.

Three detection strategies behind one shared interface:
1. forecasting-based detection (primary) — tuned quantile-band forecaster,
2. embedding-based classification — window embeddings + lightweight head,
3. zero-shot baseline — untrained quantile-residual control.
"""

from .evaluate import evaluate_strategy, load_evaluation
from .strategies import (EmbeddingHeadDetector, ForecastingDetector, ZeroShotDetector,
                         load_detector, save_detector)
from .train import train

__all__ = [
    "EmbeddingHeadDetector", "ForecastingDetector", "ZeroShotDetector",
    "evaluate_strategy", "load_detector", "load_evaluation", "save_detector", "train",
]
