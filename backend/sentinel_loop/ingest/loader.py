"""Ingest mixed incident inputs and normalize them into IncidentRecord JSONL.

Accepted inputs, per incident bundle (one subdirectory of the input dir):
- record.json          : explicit {incident_id, timestamp_start, timestamp_end,
                          severity_hint, linked_records} (ISO-8601 timestamps)
- *.log / *.txt / *.md : logs, tickets, postmortems (Markdown/PDF text/DOCX text)
- *.chat.json          : chat threads: [{"ts": ..., "user": ..., "text": ...}]
- metrics/*.csv        : monitoring metric exports, header `timestamp,value`
                         (one file per metric series; also accepts wide format
                          `timestamp,<metric_a>,<metric_b>,...`)

Every bundle becomes one IncidentRecord in <out>/records.jsonl, with all text
concatenated into raw_text and metric files listed in metric_sources. For every
incident, all sources are timestamp-bounded (record timestamps, or min/max of
metric timestamps when no record.json exists).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from ..schemas import IncidentRecord, SourceType

TEXT_SUFFIXES = {".log", ".txt", ".md"}
SOURCE_BY_SUFFIX = {
    ".log": SourceType.log,
    ".txt": SourceType.ticket,
    ".md": SourceType.postmortem,
}


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _metric_files(bundle: Path) -> list[str]:
    """Return metric source paths relative to the bundle, splitting wide CSVs."""
    found: list[str] = []
    mdir = bundle / "metrics"
    candidates = sorted(mdir.glob("*.csv")) if mdir.is_dir() else []
    candidates += sorted(p for p in bundle.glob("*.csv") if p.parent == bundle)
    for csv_path in candidates:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh), [])
        cols = [c.strip() for c in header[1:] if c.strip()]
        if len(cols) <= 1:
            found.append(str(csv_path.relative_to(bundle)))
        else:  # wide format -> one logical source per column
            for c in cols:
                found.append(f"{csv_path.relative_to(bundle)}#{c}")
    return found


def _metric_time_range(bundle: Path) -> tuple[datetime | None, datetime | None]:
    lo: datetime | None = None
    hi: datetime | None = None
    mdir = bundle / "metrics"
    csvs = sorted(mdir.glob("*.csv")) if mdir.is_dir() else []
    csvs += sorted(p for p in bundle.glob("*.csv") if p.parent == bundle)
    for csv_path in csvs:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if not row or row[0].strip().lower() in ("timestamp", "ts", "time"):
                    continue
                try:
                    ts = _parse_ts(row[0].strip())
                except (ValueError, IndexError):
                    continue
                lo = ts if lo is None or ts < lo else lo
                hi = ts if hi is None or ts > hi else hi
    return lo, hi


def _read_text(bundle: Path) -> tuple[str, SourceType]:
    parts: list[str] = []
    dominant = SourceType.mixed
    for suffix in sorted(TEXT_SUFFIXES):
        for p in sorted(bundle.rglob(f"*{suffix}")):
            if "metrics" in p.parts:
                continue
            parts.append(f"--- {p.name} ---\n{p.read_text(encoding='utf-8', errors='replace')}")
            dominant = SOURCE_BY_SUFFIX.get(suffix, dominant)
    for p in sorted(bundle.rglob("*.chat.json")):
        try:
            msgs = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        lines = [f"[{m.get('ts', '')}] {m.get('user', '?')}: {m.get('text', '')}" for m in msgs]
        parts.append(f"--- {p.name} ---\n" + "\n".join(lines))
        dominant = SourceType.chat
    return "\n\n".join(parts), dominant


def ingest_bundle(bundle: Path) -> IncidentRecord:
    record_path = bundle / "record.json"
    meta: dict = {}
    if record_path.exists():
        meta = json.loads(record_path.read_text(encoding="utf-8"))
    raw_text, source_type = _read_text(bundle)
    if meta.get("raw_text"):
        raw_text = (raw_text + "\n\n" + str(meta["raw_text"])).strip()

    incident_id = meta.get("incident_id") or bundle.name
    mlo, mhi = _metric_time_range(bundle)
    ts_start = _parse_ts(meta["timestamp_start"]) if meta.get("timestamp_start") else mlo
    ts_end = _parse_ts(meta["timestamp_end"]) if meta.get("timestamp_end") else mhi
    if ts_start is None or ts_end is None:
        raise ValueError(f"incident bundle {bundle} has no timestamps (record.json or metric CSVs required)")
    return IncidentRecord(
        incident_id=str(incident_id),
        source_type=source_type,
        timestamp_start=ts_start,
        timestamp_end=ts_end,
        raw_text=raw_text,
        metric_sources=_metric_files(bundle),
        severity_hint=meta.get("severity_hint"),
        linked_records=[str(x) for x in meta.get("linked_records", [])],
    )


def ingest_directory(input_dir: str | Path, out_dir: str | Path) -> list[IncidentRecord]:
    """Ingest every incident bundle under input_dir -> <out_dir>/records.jsonl."""
    input_dir = Path(input_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[IncidentRecord] = []
    bundles = sorted(p for p in input_dir.iterdir() if p.is_dir())
    if not bundles and (input_dir / "record.json").exists():
        bundles = [input_dir]
    for bundle in bundles:
        records.append(ingest_bundle(bundle))
    write_records(records, out_dir / "records.jsonl")
    return records


def write_records(records: list[IncidentRecord], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(r.model_dump_json() + "\n")


def load_records(path: str | Path) -> list[IncidentRecord]:
    path = Path(path)
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(IncidentRecord.model_validate_json(line))
    return records
