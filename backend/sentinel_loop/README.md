# Sentinel-Loop — Timestral incident detection system

Production pipeline built from `system_create_prompt.md`. Division of labor is
fixed and never swapped:

| Stage | Model | Role |
|---|---|---|
| Metric extraction | **Mistral Large** (La Plateforme, Batch API) | analyzes raw incident data, extracts numeric metrics |
| Labeling / guarding | **Shieldstral** (API) | window labels, quality gates, PII filtering, alert verification |
| Detection | **Timestral** (self-hosted, 5M default) | the ONLY detector; the ONLY tuned model |

```
raw incident data ──▶ ingest ──▶ extract-metrics ──▶ label-windows ──▶ build-dataset
 (logs/tickets/         (Mistral Large          (Shieldstral          (aligned series,
  postmortems/chat/     -> metrics.yaml)         labels + PII)         detection/forecast
  metric exports)                                                   sets, incident splits)
                                                                        │
              ┌─────────────────────────────────────────────────────────┘
              ▼
   train-detection (forecasting, primary) / train-embedding-head / zero-shot control
              │
              ▼
   evaluate (held-out incidents: P/R/F1, lead time, FP/1000 window-h, quantile loss)
              │
              ▼
   serve /watch — live detection chain per window (serve/chain.py, fixed order):
              │     1. Timestral detects (self-hosted, FastAPI)
              │     2. Shieldstral verifies -> unverified candidates suppressed (false-positive gate)
              │     3. Mistral Large explains the fired alert -> explanation PRINTED + stored
              │                                               │
              └────────────── feedback ◀───────────────────────┘
                    (reviews + drift/new-incident/scheduled retrain triggers)

Full system architecture: see `architecture.md` at the repo root.
```

## Layout

```
backend/sentinel_loop/
  schemas.py            # pydantic schemas for every stage boundary
  config.py             # versioned config: metrics.yaml, policies, thresholds
  mistral_client.py     # Large (chat+batch) / Shieldstral (pair, policy, PII) wrapper, retries, cost log
  timestral.py          # self-hosted Timestral backend protocol + local reference implementation
  ingest/               # any incident type -> IncidentRecord JSONL
  extract_metrics/      # Mistral Large -> per-incident JSON + merged metrics.yaml
  labeling/             # Shieldstral-validated window labels + SQLite labeling store
  dataset/              # aligned series, detection/forecasting sets, incident-level splits
  tuning/               # forecasting / embedding-head / zero-shot detectors + evaluation
  serve/                # FastAPI /sentinel/watch + shadow mode
  feedback/             # reviews + retrain triggers back into the labeling store
  policies/             # labeling / pii / alert-verification policies (versioned YAML)
  metrics.yaml          # monitoring contract outline + thresholds (never hardcoded)
  examples/synthetic.py # 2 metrics + 2 incidents (incl. a deploy-related hard negative)
  tests/                # mocked-API unit tests + end-to-end synthetic test
  docker-compose.yml    # watcher + labeling store
```

## Quickstart (synthetic example, end to end)

From `backend/` with the repo `.venv` active:

```bash
# 0. generate the synthetic incident set (2 metrics + 2 incidents)
python -m sentinel_loop.examples.synthetic --out data/raw

# 1..4 offline-capable stages (extract/label need MISTRAL_API_KEY)
python -m sentinel_loop ingest            --data-dir data
python -m sentinel_loop extract-metrics   --data-dir data   # Mistral Large, Batch API
python -m sentinel_loop label-windows     --data-dir data   # Shieldstral validation + PII
python -m sentinel_loop build-dataset     --data-dir data

# 5..6 train + evaluate (self-hosted Timestral side; held-out incidents only for eval)
python -m sentinel_loop train-detection      --data-dir data   # forecasting (primary)
python -m sentinel_loop train-embedding-head --data-dir data
python -m sentinel_loop train-zero-shot      --data-dir data   # control baseline
python -m sentinel_loop evaluate --data-dir data --strategy forecasting

# 7. serve (self-hosted) and shadow-run
python -m sentinel_loop serve       --data-dir data --port 8001
python -m sentinel_loop shadow-run  --data-dir data --strategy forecasting

# 8. issue forecasting: multi-step forecast + first expected band breach (lead time)
python -m sentinel_loop forecast    --data-dir data --strategy forecasting --steps 12
```

The watcher is also mounted into the live website app under `/sentinel/*`
(`POST /sentinel/watch`, `POST /sentinel/forecast`, `POST /sentinel/feedback`,
`GET /sentinel/alerts`, `GET /sentinel/health`).

```bash
# watch a live stream (shadow mode first: alerts logged, not sent)
curl -X POST localhost:8001/sentinel/watch -H 'content-type: application/json' -d '{
  "shadow": true,
  "streams": {"api_latency_ms": [{"ts": "2026-01-10T03:00:00Z", "value": 95.0}]}
}'

# forecast the next steps and the first step expected to breach the normal band
curl -X POST localhost:8001/sentinel/forecast -H 'content-type: application/json' -d '{
  "steps": 12,
  "streams": {"api_latency_ms": [{"ts": "2026-01-10T03:00:00Z", "value": 95.0}]}
}'
```

`POST /sentinel/forecast` returns `{strategy, steps, forecasts: {metric: [ForecastPoint]},
issues: [IssueForecast]}`; an `IssueForecast` is the first forecast step whose predicted
value leaves the current normal band, with `expected_in_minutes` lead time.

## Tests

```bash
cd backend && ../.venv/bin/python -m pytest sentinel_loop/tests -q
```

All Mistral API calls are mocked in tests; the synthetic end-to-end test runs
the whole pipeline offline.

## Constraints honored

- Only Timestral detects and is tuned; Large extracts, Shieldstral labels/verifies/guards.
- All text entering labeling/training stores passes PII filtering first (Shieldstral policy mode, regex fallback).
- `metrics.yaml`, policies, thresholds live in versioned config — never hardcoded.
- Start with the smallest Timestral (5M); scale up only on the held-out incident eval.
- Detection is self-hosted on minimal hardware; only extraction/labeling call APIs.

## Real Timestral weights

`timestral.py` defines the `TimestralBackend` protocol. The bundled
`LocalTimestralBackend` (numpy rolling-quantile forecaster + window embeddings)
implements it so the pipeline runs without weights. To use a real checkpoint,
im plement the same protocol against Mistral Forge fine-tuning or open-weights
inference and drop it into `tuning/strategies.py` — roles stay unchanged.
