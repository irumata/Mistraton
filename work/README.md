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
`labels.db`, `dataset/`, `artifacts/`, `extractions/`, `archive/`, `cost_log.jsonl`
are pipeline outputs. Regenerate from `backend/` with `SENTINEL_DATA_DIR=../work`:
`ingest -> label-windows -> build-dataset -> train-detection/-embedding-head/-zero-shot -> evaluate -> forecast`.

## Archive + demo flow (website / CLI)
```bash
# 1. package this folder into a portable, hash-verified archive
python -m sentinel_loop prepare-archive --src ../work        # -> <data-dir>/archive/*.zip
#    (website: POST /sentinel/archive/prepare, GET /sentinel/archive/download?name=...)

# 2. on the target host: download + apply the archive, then let Mistral Large 4
#    analyze the bundles and write the metric contract (metrics.yaml)
python -m sentinel_loop apply-archive                        # verify sha256, extract to raw/, ingest
#    (website: POST /sentinel/archive/upload)
python -m sentinel_loop extract-metrics --no-batch           # mistral-large-4 per incident (Batch API default)

# 3. Shieldstral labels/validates windows (+ PII filtering)
python -m sentinel_loop label-windows                        # all incidents (offline PII fallback)
python -m sentinel_loop label-windows --incident aiid-1492 --max-windows 40   # with API: real validation

# 4. Timestral is fitted into the detector
python -m sentinel_loop build-dataset && python -m sentinel_loop train-detection && python -m sentinel_loop evaluate

# 5. demo: replay a stored incident through the live chain (timestral -> shieldstral -> large)
python -m sentinel_loop demo --incident aiid-1492 --no-shadow --scope incident --max-windows 40
python -m sentinel_loop demo --incident aiid-1361 --no-shadow --scope full   # hard negative gets suppressed
#    (website: Watch tab -> "Run demo", or POST /sentinel/demo)
```

## Caveats
- AIID incident dates are date-granular; `record.json` timestamps use T00:00Z,
  so lead-time numbers in eval reports are approximate.
- MIT harm-severity scores are not API-exposed; `severity_hint` tags were
  assigned from incident content (deaths/physical harm => 4–5).
- The nginx sample contains synthetic IP addresses; PII filtering runs before
  labeling/training stores.
