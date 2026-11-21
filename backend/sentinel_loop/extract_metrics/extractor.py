"""Extract numeric metric definitions from incident bundles with Mistral Large.

For each IncidentRecord, Large receives the full record bundle (raw text +
metric export summaries) and returns structured JSON (ExtractionOutput).
Outputs: per-incident JSON in <data-dir>/extractions/ and a merged global
<data-dir>/metrics.yaml — the monitoring contract later stages consume.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

from ..config import MetricsConfig, Settings, resolve_config
from ..ingest.loader import load_records
from ..mistral_client import MistralClients
from ..schemas import ExtractionOutput, IncidentRecord

EXTRACT_SYSTEM_PROMPT = """You are the metric-extraction stage of an incident-detection system.
You receive one incident bundle (raw logs, tickets, postmortems, chat, metric export summaries).
Output STRICT JSON only, matching this schema:
{
  "incident_id": str,
  "incident_summary": str,
  "metric_definitions": [{
    "name": str, "source": str, "aggregation": str,
    "expected_normal_range": [str, str], "observed_bad_range": [str, str],
    "lead_time_minutes": number, "why_relevant": str
  }],
  "precursor_signals": [{"metric": str, "pattern": str, "lead_time_minutes": number}],
  "timeline": [{"timestamp": str, "description": str}],
  "root_cause_candidates": [str],
  "prevention_actions": [str]
}
Rules: metric_definitions must reference series actually present in the metric
exports; lead_time_minutes is the measured gap between precursor onset and
incident start; expected_normal_range/observed_bad_range are stringified numbers.
"""


def _metric_summaries(record: IncidentRecord, data_dir: Path) -> str:
    """Summarize the metric exports referenced by this record (name, span, stats)."""
    lines = []
    for src in record.metric_sources:
        if "#" in src:
            rel, col = src.split("#", 1)
        else:
            rel, col = src, None
        csv_path = None
        for bundle in sorted((data_dir / "raw").iterdir()) if (data_dir / "raw").is_dir() else []:
            cand = bundle / rel
            if cand.exists():
                csv_path = cand
                break
        if csv_path is None:
            continue
        name = col or csv_path.stem
        vals: list[float] = []
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            idx = header.index(col) if col and col in header else 1
            for row in reader:
                if len(row) > idx:
                    try:
                        vals.append(float(row[idx]))
                    except ValueError:
                        pass
        if vals:
            lines.append(
                f"- {name}: n={len(vals)} min={min(vals):.4g} max={max(vals):.4g} mean={sum(vals)/len(vals):.4g}"
            )
    return "\n".join(lines) or "(no metric exports found)"


def build_user_payload(record: IncidentRecord, data_dir: Path) -> str:
    return (
        f"incident_id: {record.incident_id}\n"
        f"window: {record.timestamp_start.isoformat()} .. {record.timestamp_end.isoformat()}\n"
        f"severity_hint: {record.severity_hint or 'n/a'}\n\n"
        f"METRIC EXPORT SUMMARIES:\n{_metric_summaries(record, data_dir)}\n\n"
        f"RAW RECORD BUNDLE:\n{record.raw_text[:12000]}"
    )


def parse_extraction(incident_id: str, raw_json: str) -> ExtractionOutput:
    data = json.loads(raw_json)
    data.setdefault("incident_id", incident_id)
    return ExtractionOutput.model_validate(data)


def extract_all(
    data_dir: str | Path,
    clients: MistralClients,
    config: MetricsConfig | None = None,
) -> list[ExtractionOutput]:
    """Run extraction for every ingested record; write per-incident JSON and
    the merged metrics.yaml monitoring contract into the data dir."""
    data_dir = Path(data_dir)
    records = load_records(data_dir / "records.jsonl")
    out_dir = data_dir / "extractions"
    out_dir.mkdir(parents=True, exist_ok=True)

    payloads = [build_user_payload(r, data_dir) for r in records]
    raw_outputs = clients.large_extract_batch(EXTRACT_SYSTEM_PROMPT, payloads)

    outputs: list[ExtractionOutput] = []
    for record, raw in zip(records, raw_outputs):
        extraction = parse_extraction(record.incident_id, raw)
        outputs.append(extraction)
        (out_dir / f"{record.incident_id}.json").write_text(extraction.model_dump_json(indent=2), encoding="utf-8")

    merged_path = merge_metrics_yaml(outputs, data_dir, config)
    return outputs


def merge_metrics_yaml(
    outputs: list[ExtractionOutput],
    data_dir: str | Path,
    config: MetricsConfig | None = None,
) -> Path:
    """Merge all metric definitions into the global monitoring contract."""
    data_dir = Path(data_dir)
    if config is None:
        config, _ = resolve_config(data_dir, None)
    seen: dict[str, dict] = {m.name: m.model_dump() for m in config.metrics}
    for out in outputs:
        for md in out.metric_definitions:
            seen[md.name] = md.model_dump()  # later incidents can refine ranges
    merged = MetricsConfig(
        version=config.version,
        window=config.window,
        metrics=[yaml.safe_load(yaml.safe_dump(v)) for v in seen.values()],
        thresholds=config.thresholds,
        timestral=config.timestral,
        policies_dir=config.policies_dir,
    )
    path = data_dir / "metrics.yaml"
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(json.loads(merged.model_dump_json()), fh, sort_keys=False)
    return path


def load_extractions(data_dir: str | Path) -> list[ExtractionOutput]:
    data_dir = Path(data_dir)
    out: list[ExtractionOutput] = []
    for p in sorted((data_dir / "extractions").glob("*.json")):
        out.append(ExtractionOutput.model_validate_json(p.read_text(encoding="utf-8")))
    return out
