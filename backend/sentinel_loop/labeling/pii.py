"""Regex fallback PII filter.

In production, Shieldstral performs PII filtering (policy mode) before any
text enters the labeling/training stores. This regex pass is the offline
fallback so the pipeline still strips obvious PII without an API key.
"""

from __future__ import annotations

import re

_PATTERNS = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "[IP]"),
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[CARD]"),
    (re.compile(r"\b[A-Za-z0-9_-]{20,}\b"), "[TOKEN]"),  # API keys / tokens
]


def regex_pii_filter(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
