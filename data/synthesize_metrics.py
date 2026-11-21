"""Generate synthetic metric series for text-only incident bundles.

For every bundle under work/raw/ that has no metrics/ dir yet, writes
metrics/api_latency_ms.csv and metrics/error_rate.csv (1-minute samples from
3h before incident start to 1h after incident end): a precursor ramp in the
25 min before start, a spike during [start, end], and a 30 min degraded decay.
Every 4th bundle also gets a planned-deploy hard-negative spike 2h before start
(looks like a precursor in raw shape but is normal operation).

Amplitude scales with the bundle's severity_hint (catastrophic > severe > other).
Deterministic (seeded). Idempotent: skips bundles that already have metrics/.

Usage: python3 work/synthesize_metrics.py
"""

from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
LEAD = timedelta(minutes=25)
DEGRADED = timedelta(minutes=30)
PRE = timedelta(hours=3)
POST = timedelta(hours=1)


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _amplitude(severity_hint: str) -> tuple[float, float]:
    s = (severity_hint or "").lower()
    if "catastrophic" in s:
        return 2500.0, 0.25
    if "severe" in s:
        return 1800.0, 0.20
    return 900.0, 0.08


def synthesize_bundle(bundle: Path, seed: int) -> bool:
    if (bundle / "metrics").is_dir() and list((bundle / "metrics").glob("*.csv")):
        return False
    rec = json.loads((bundle / "record.json").read_text())
    start, end = _parse(rec["timestamp_start"]), _parse(rec["timestamp_end"])
    lat_spike, err_spike = _amplitude(rec.get("severity_hint"))
    rng = random.Random(seed)
    hard_neg_at = start - timedelta(hours=2) if seed % 4 == 0 else None

    mdir = bundle / "metrics"
    mdir.mkdir(parents=True, exist_ok=True)
    t0, t1 = start - PRE, end + POST
    rows = []
    t = t0
    while t <= t1:
        lat = 80.0 + rng.uniform(-8, 8)
        err = max(0.0, 0.001 + rng.uniform(-0.0004, 0.0004))
        if hard_neg_at is not None and hard_neg_at <= t < hard_neg_at + timedelta(minutes=5):
            lat += lat_spike * 0.5  # planned deploy: hard negative
        if start - LEAD <= t < start:
            frac = (t - (start - LEAD)) / LEAD
            lat += lat_spike * frac
            err += err_spike * frac
        elif start <= t < end:
            lat += lat_spike * (1.0 + 0.3 * rng.uniform(-1, 1))
            err += err_spike * (1.0 + 0.3 * rng.uniform(-1, 1))
        elif end <= t < end + DEGRADED:
            frac = (t - end) / DEGRADED
            lat += lat_spike * (1 - frac) * 0.6
            err += err_spike * (1 - frac) * 0.6
        rows.append((t, round(lat, 3), round(err, 5)))
        t += timedelta(minutes=1)

    for name, idx in (("api_latency_ms", 1), ("error_rate", 2)):
        with open(mdir / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp", "value"])
            for t, *vals in rows:
                w.writerow([t.isoformat(), vals[idx - 1]])
    return True


def main() -> int:
    made = []
    for i, bundle in enumerate(sorted(p for p in RAW.iterdir() if p.is_dir())):
        if synthesize_bundle(bundle, seed=1000 + i):
            made.append(bundle.name)
    print(f"synthesized metrics for {len(made)} bundles: {made}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
