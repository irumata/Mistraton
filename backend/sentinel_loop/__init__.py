"""Sentinel-Loop: Timestral incident detection system.

Division of labor (never swapped):
- Mistral Large (API)   -> analyzes raw incident data, extracts numeric metrics.
- Shieldstral (API)     -> labels/verifies/guards: window labels, PII filtering, alert verification.
- Timestral (self-hosted, small) -> the ONLY detector; the ONLY model that is tuned.

Pipeline: ingest -> extract-metrics -> label-windows -> build-dataset
          -> train-detection / train-embedding-head -> evaluate -> serve / shadow-run
          -> feedback loop back into the labeling store.
"""

__version__ = "0.1.0"

PIPELINE_STAGES = [
    "ingest",
    "extract-metrics",
    "label-windows",
    "build-dataset",
    "train-detection",
    "train-embedding-head",
    "evaluate",
    "serve",
    "shadow-run",
]
