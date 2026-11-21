# work/ — Sentinel-Loop dataset (source data only)

Collected + generated input data for `backend/sentinel_loop/`, in the exact
bundle format `ingest` expects (`raw/<incident_id>/` with `record.json`, text
files, `metrics/*.csv`).

## Contents
- `raw/aiid-*` (11 bundles): high-severity 2026 AI incidents pulled from the AI
  Incident Database (the MIT AI Incident Tracker's source data,
  incidentdatabase.ai/cite/<id>), each with `record.json` (id, timestamps,
  severity hint on the MIT 1–5 harm scale, source link) + `report.md`
  (description, alleged systems, harmed parties, MIT machine classification).
  Their `metrics/*.csv` are **synthetic** (see below).
- `raw/hf-nginx-logs`: sample of nginx access logs from the public Hugging Face
  dataset `mindweave/web-server-logs` (CC BY-NC 4.0; free 5k-row sample; README
  states injected DDoS + database-outage anomalies) — the only bundle with real
  metric series (`response_time_ms`, `status_code`, `error_5xx`).
- `raw/hf-agent-failures`: 15 labeled LLM-agent failure events sampled from
  `GXCafe/ai-agent-failure-logs` (CC BY 4.0), as `failures.chat.json`.
- `raw/hf-security-incident-2026-07`: Hugging Face's own July 2026 security
  incident postmortem (huggingface.co/blog/security-incident-july-2026).
- `records.jsonl`: output of `ingest` (14 normalized `IncidentRecord`s).
- `metrics.yaml`: monitoring contract (hand-written stand-in for the
  `extract-metrics` stage, which needs `MISTRAL_API_KEY`).
- `synthesize_metrics.py`: generated the synthetic `metrics/*.csv` for the
  text-only bundles (precursor ramp 25 min before incident start, spike during
  the incident, 30 min degraded decay, planned-deploy hard negatives; amplitude
  scaled by severity hint; deterministic seeds). Re-run idempotently.

## Derived artifacts (NOT committed — regenerate)
`labels.db`, `dataset/`, `artifacts/` are pipeline outputs. Regenerate from
`backend/` with `SENTINEL_DATA_DIR=../work`:
`ingest -> label-windows -> build-dataset -> train-detection/-embedding-head/-zero-shot -> evaluate -> forecast`.

## Caveats
- AIID incident dates are date-granular; `record.json` timestamps use T00:00Z,
  so lead-time numbers in eval reports are approximate.
- MIT harm-severity scores are not API-exposed; `severity_hint` tags were
  assigned from incident content (deaths/physical harm => 4–5).
- The nginx sample contains synthetic IP addresses; PII filtering runs before
  labeling/training stores.
