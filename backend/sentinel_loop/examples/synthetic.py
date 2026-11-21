"""Generate a tiny synthetic incident set: 2 metrics + 2 incidents.

Timeline per incident bundle: 6h of 1-minute samples.
- inc-001: latency ramps up over a 25-minute precursor, spikes during a 30m
  incident, decays over 30m degraded. error_rate rises in parallel.
- inc-002: shorter incident (20m, 15m lead) plus a deploy-related latency
  spike earlier that is a HARD NEGATIVE (looks like a precursor in raw shape
  but is labeled normal — planned deploy).

Usage: python -m sentinel_loop.examples.synthetic --out data/raw
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

T0 = datetime(2026, 1, 10, 0, 0, 0, tzinfo=timezone.utc)
SAMPLING = timedelta(minutes=1)


def _series(incident_start: datetime, incident_end: datetime, lead: timedelta,
            spike: tuple[float, float], hard_negative_at: datetime | None = None,
            hard_negative_amp: float = 0.0, seed: int = 42) -> tuple[list, list]:
    """Return (timestamps, latency, error_rate) lists for a 6h window."""
    rng = random.Random(seed)
    ts = T0
    end = T0 + timedelta(hours=6)
    times, latency, errors = [], [], []
    while ts <= end:
        t = ts
        lat = 80 + rng.uniform(-8, 8)
        err = max(0.0, 0.001 + rng.uniform(-0.0004, 0.0004))
        # hard negative: planned deploy spike, NOT an incident
        if hard_negative_at is not None and hard_negative_at <= t < hard_negative_at + timedelta(minutes=5):
            lat += hard_negative_amp
        # precursor ramp
        if incident_start - lead <= t < incident_start:
            frac = (t - (incident_start - lead)) / lead
            lat += spike[0] * frac
            err += spike[1] * frac
        # incident
        elif incident_start <= t < incident_end:
            lat += spike[0] * (1.0 + 0.3 * rng.uniform(-1, 1))
            err += spike[1] * (1.0 + 0.3 * rng.uniform(-1, 1))
        # degraded decay
        elif incident_end <= t < incident_end + timedelta(minutes=30):
            frac = (t - incident_end) / timedelta(minutes=30)
            lat += spike[0] * (1 - frac) * 0.6
            err += spike[1] * (1 - frac) * 0.6
        times.append(t)
        latency.append(round(lat, 3))
        errors.append(round(err, 5))
        ts += SAMPLING
    return times, latency, errors


def _write_bundle(out: Path, incident_id: str, start: datetime, end: datetime,
                  lead: timedelta, spike: tuple[float, float], hard_negative: bool, seed: int):
    b = out / incident_id
    (b / "metrics").mkdir(parents=True, exist_ok=True)
    times, latency, errors = _series(
        start, end, lead, spike,
        hard_negative_at=(start - timedelta(hours=2)) if hard_negative else None,
        hard_negative_amp=spike[0] * 0.5 if hard_negative else 0.0,
        seed=seed,
    )
    for name, vals in (("api_latency_ms", latency), ("error_rate", errors)):
        with open(b / "metrics" / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp", "value"])
            for t, v in zip(times, vals):
                w.writerow([t.isoformat(), v])
    (b / "record.json").write_text(json.dumps({
        "incident_id": incident_id,
        "timestamp_start": start.isoformat(),
        "timestamp_end": end.isoformat(),
        "severity_hint": "SEV2" if incident_id == "inc-001" else "SEV3",
        "linked_records": [],
    }, indent=2), encoding="utf-8")
    (b / "ticket.md").write_text(
        f"# {incident_id}\n\nAPI latency degraded for users in eu-west. "
        f"Onset around {start.isoformat()}, resolved {end.isoformat()}. "
        "Contact: oncall@example.com\n", encoding="utf-8")
    (b / "postmortem.md").write_text(
        f"# Postmortem {incident_id}\n\nTimeline: precursor symptoms from "
        f"{(start - lead).isoformat()} (error_rate gradual rise), incident "
        f"{start.isoformat()}..{end.isoformat()}, degraded until "
        f"{(end + timedelta(minutes=30)).isoformat()}.\nRoot cause: connection pool exhaustion.\n",
        encoding="utf-8")
    (b / "chat.json").write_text(json.dumps([
        {"ts": (start - lead).isoformat(), "user": "alice", "text": "error_rate creeping up, watch latency"},
        {"ts": start.isoformat(), "user": "bob", "text": "p99 latency 2s+, paging"},
        {"ts": end.isoformat(), "user": "alice", "text": "mitigated, watching recovery"},
    ], indent=2), encoding="utf-8")


def generate_synthetic(out: str | Path) -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    s1 = T0 + timedelta(hours=3)
    _write_bundle(out, "inc-001", s1, s1 + timedelta(minutes=30),
                  lead=timedelta(minutes=25), spike=(1800.0, 0.2), hard_negative=False, seed=42)
    s2 = T0 + timedelta(hours=4)
    _write_bundle(out, "inc-002", s2, s2 + timedelta(minutes=20),
                  lead=timedelta(minutes=15), spike=(900.0, 0.08), hard_negative=True, seed=7)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="generate the synthetic example incident set")
    parser.add_argument("--out", default="data/raw", help="output dir for incident bundles")
    args = parser.parse_args(argv)
    path = generate_synthetic(args.out)
    print(f"synthetic example written to {path} (inc-001, inc-002)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
