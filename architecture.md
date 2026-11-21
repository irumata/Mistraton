# Sentinel-Loop — Architecture

Production incident-detection system built from `system_create_prompt.md`, living in
`backend/sentinel_loop/`. It is built around a **fixed division of labor across three
Mistral model families — roles are never swapped**:

| Model | Where | Role |
|---|---|---|
| **Timestral** (small, self-hosted; default `5M`, scale-up `37M`/`72M`/`209M`) | local / Forge / HF checkpoint | **the only detector, the only tuned model** — scores metric windows, live and in training |
| **Shieldstral** (La Plateforme API + self-hosted) | API | **labeling, guarding, verification** — window labels, quality gates, PII filtering, alert verification (false-positive gate) |
| **Mistral Large** (`mistral-large-latest` by default, `SENTINEL_LARGE_MODEL`) | La Plateforme API (chat + Batch) | **analysis & explanation** — extracts numeric metrics from raw incident data; explains fired alerts in plain language |

Only Timestral runs self-hosted on minimal hardware; only the extraction, labeling,
verification and explanation stages call APIs.

## 1. Offline pipeline (build the detector)

```
raw incident data ─▶ ingest ─▶ extract-metrics ─▶ label-windows ─▶ build-dataset ─▶ train-detection ─▶ evaluate
(logs, tickets,      (normalize   (Mistral Large     (Shieldstral     (aligned series,   (Timestral        (held-out
 postmortems, chat,   to JSONL     -> metrics.yaml    labels + PII     detection/         fine-tune;         incidents:
 metric exports)      records)     monitoring        + validation)    forecast sets,     3 strategies)     P/R/F1, lead
                                contract)                          incident splits)                     time, FP rate)
```

| Stage | Code | Model | Output |
|---|---|---|---|
| Ingest | `backend/sentinel_loop/ingest/loader.py` | — | `records.jsonl` of `IncidentRecord` |
| Metric extraction | `backend/sentinel_loop/extract_metrics/extractor.py` | Mistral Large (Batch API) | per-incident `ExtractionOutput` JSON + merged `<data-dir>/metrics.yaml` (the monitoring contract) |
| Labeling | `backend/sentinel_loop/labeling/labeler.py`, `labeling/store.py`, `labeling/pii.py` | Shieldstral (pair/policy/PII) | `LabelledWindow` rows (`normal`/`precursor`/`incident`/`degraded`) in SQLite/Postgres labeling store |
| Dataset assembly | `backend/sentinel_loop/dataset/assemble.py` | — | detection + forecasting window sets, incident-level train/val/test splits |
| Tuning | `backend/sentinel_loop/tuning/{train,finetune,strategies,evaluate}.py`, `timestral.py` | Timestral only | `artifacts/detector_<strategy>.json`, `eval_<strategy>.json` |
| Feedback | `backend/sentinel_loop/feedback/loop.py` | — | reviews + retrain triggers (scheduled / new incident / drift) in the labeling store |

Three Timestral detection strategies share one interface
(`BaseDetector.detect(...) -> DetectionEvent | None`), evaluated against each other:

1. **forecasting** (primary) — fine-tune Timestral to forecast each stream on
   normal-condition data; detection = observed outside the predicted quantile band,
   sustained for `persistence_windows`.
2. **embedding** — Timestral window representations + a lightweight softmax head.
3. **zero-shot** — untrained quantile-residual control the tuned models must beat.

Evaluation is always at incident level on the held-out test split: precision/recall/F1
per window state, mean lead time (minutes), false positives per 1000 window-hours, and
mean quantile loss on normal data.

## 2. Live detection chain (serve) — Timestral → Shieldstral → Large

`POST /sentinel/watch` (`backend/sentinel_loop/serve/watcher.py`) consumes live metric
streams defined in `metrics.yaml` and runs every new sliding window through
`DetectionChain` (`backend/sentinel_loop/serve/chain.py`). **The order is fixed:**

```
window ─▶ 1. TIMESTRAL   self-hosted detector scores the window
                    │  candidate DetectionEvent {metric, model_confidence, window_state, lead_time_minutes}
                    │  (dropped if confidence < thresholds.alert_confidence)
                    ▼
           2. SHIELDSTRAL  pair-mode policy verification of the candidate
                    │  "Does this evidence support firing an alert for this metric window?"
                    │  NO  -> candidate SUPPRESSED (false positive avoided, not fired, no explanation)
                    │  (skipped in shadow mode; fails closed if no API client is available)
                    ▼
           3. LARGE       Mistral Large explains the fired alert
                    │  AlertExplanation {summary, why_timestral_flagged, why_shieldstral_verified,
                    │                   likely_causes, recommended_actions}
                    ▼
           explanation is PRINTED (stdout + log) and stored on the AlertEvent
```

- `GET /sentinel/health`, `POST /sentinel/watch`, `POST /sentinel/feedback`,
  `GET /sentinel/alerts`.
- `WatchResponse = {events, alerts, shadow, suppressed, chains}` — `chains` is the
  per-window trace of stages (`ChainStage{stage, model, passed, detail, seconds}`), so
  every fired/suppressed window is auditable.
- **Shadow mode** (`"shadow": true`): the chain runs, alerts are logged with
  `verified: null` and **not sent**, until precision on reviewed alerts passes
  `thresholds.shadow_precision_threshold`.
- **Feedback loop**: `POST /sentinel/feedback` records reviewer confirm/reject;
  `reviewed_precision()` over human-reviewed alerts feeds the drift trigger
  (`thresholds.drift_precision_threshold`) → retraining.

Chain behavior is versioned config, never hardcoded (`chain:` block in `metrics.yaml`):

```yaml
chain:
  verify_with_shieldstral: true   # stage-2 false-positive gate
  suppress_unverified: true        # Shieldstral-rejected candidates are dropped
  explain_with_large: true         # stage-3 explanation on fired alerts
  explanation_max_chars: 4000
```

## 3. Mistral API client wrapper

`backend/sentinel_loop/mistral_client.py` — one wrapper, injectable `transport`
(mockable in tests), retries with exponential backoff, append-only cost log
(`<data-dir>/cost_log.jsonl`):

| Method | Model | Mode | Used by |
|---|---|---|---|
| `large_extract` / `large_extract_batch` | Large | chat / Batch API, JSON | extract-metrics |
| `large_explain_alert` | Large | chat, JSON (`AlertExplanation`) | detection chain stage 3 |
| `shieldstral_pair` | Shieldstral | pair (YES/NO) | labeling validation, chain stage 2 |
| `shieldstral_policy` / `shieldstral_pii_filter` | Shieldstral | policy (passes/redacted) | PII gate before any text is stored |

Timestral is **never** called through this wrapper — it is self-hosted
(`timestral.py`: `TimestralBackend` protocol + `LocalTimestralBackend` numpy reference
implementation; swap in a real checkpoint via Forge/HF without changing roles).

## 4. Schemas (every stage boundary is pydantic-validated)

`backend/sentinel_loop/schemas.py`: `IncidentRecord`, `ExtractionOutput` /
`MetricDefinition` / `PrecursorSignal`, `LabelledWindow`, `WindowSample` / `MetricPoint`,
`DetectionEvent`, `AlertEvent` (carries `verified` + the printed `explanation`),
`AlertExplanation`, `ChainStage` / `ChainResult`, `EvaluationReport`.
All timestamps are timezone-aware UTC.

## 5. Config, policies, thresholds (versioned, never hardcoded)

- `backend/sentinel_loop/metrics.yaml` — monitoring contract outline (metrics, window,
  thresholds, timestral size/backend, **chain**); `extract-metrics` writes the merged
  data-derived contract to `<data-dir>/metrics.yaml`, which later stages prefer
  (`config.resolve_config`).
- `backend/sentinel_loop/policies/` — `labeling_policy.yaml`, `pii_policy.yaml`,
  `alert_verification_policy.yaml` (versioned; versions persisted in the labeling store).
- Env settings (`config.Settings`): `MISTRAL_API_KEY`, `SENTINEL_LARGE_MODEL`,
  `SENTINEL_SHIELDSTRAL_MODEL`, `SENTINEL_DATA_DIR`, `SENTINEL_DB`,
  `SENTINEL_ARTIFACTS_DIR`, `SENTINEL_COST_LOG`.

## 6. Labeling store & persistence

`labeling/store.py` — SQLite by default (`<data-dir>/labels.db`), Postgres-ready schema:
`labelled_windows`, `policy_versions`, `alerts` (event JSON + verified + **explanation** +
review), `retrain_triggers`. All text entering the store passes Shieldstral PII
filtering first (regex fallback in `labeling/pii.py`).

## 7. CLI & deployment

```
python -m sentinel_loop ingest | extract-metrics | label-windows | build-dataset |
         train-detection | train-embedding-head | train-zero-shot | evaluate |
         serve | shadow-run | finetune-timestral      (run from backend/)
```

- `backend/sentinel_loop/docker-compose.yml` — watcher (uvicorn) + labeling-store volume.
- The watcher is also mounted into the website app under `/sentinel/*`.
- `finetune-timestral` — QLoRA fine-tune of a small open-weights checkpoint as Timestral
  on Colab/GPU (`backend/requirements-colab.txt`, `notebooks/`).
- Tests: `backend/sentinel_loop/tests/` — mocked-API unit tests, chain tests
  (`test_chain.py`), serve tests (`test_serve.py`), and an end-to-end synthetic run
  (`test_e2e_synthetic.py`, `examples/synthetic.py`: 2 metrics + incidents incl. a
  deploy-related hard negative).

## 8. Web interface (pixelated Mistral style)

The Colab **Investigate** home screen accepts a ZIP from the browser and runs a
separate archive-analysis workflow. Large 4 chooses a measured signal and
proposes how to monitor it. Shieldstral checks the early baseline, Timestral
forecasts later observations, Shieldstral verifies candidate episodes, and a
fresh Large 4 request independently reviews and explains the strongest candidates.
An alert needs both reviewers to support it. The run
reports skipped stages when measurements or candidates are unavailable.

Every run has a separate capability key and in-memory evidence, baseline,
progress, and results. Model weights are shared and read-only. A worker pool
limits concurrent jobs and individual model locks protect loaded inference
instances. Uploaded ZIPs are read in memory, and the result carries the SHA-256
of the bytes received over HTTP.

`frontend/` (React + Vite) is the operator console, served next to the API and talking
to the `/sentinel/*` endpoints mounted by `backend/colab_app.py`:

- Whole UI is styled in **Mistral AI pixel style**: `Press Start 2P` pixel font, crisp
  square brand assets (`frontend/src/components/pixel.jsx` — the yellow→red pixel "M"
  logo and pixel shields), hard 3px borders, offset block shadows, pixel-grid paper
  background, yellow/orange/red brand palette (`frontend/src/App.css`), pixel favicon.
- The **SAFETRAL** badge (header + every alert) is the visible face of the **Shieldstral
  guard layer**: green shield = verified, red = rejected/suppressed, gray = shadow mode.
- The **Watch** tab renders the live detection chain per window as pixel stage chips
  `1·TIMESTRAL → 2·SAFETRAL → 3·LARGE` with pass/fail marks and an outcome badge
  (`▶ fired` / `✕ suppressed` / `· clear`), a summary strip
  (`events / alerts fired / suppressed / mode`), and each fired alert's **printed
  Mistral Large explanation** in a scanlined pixel console. Stored alerts keep
  confirm/reject feedback buttons.

## 9. File map

```
backend/sentinel_loop/
  schemas.py            # pydantic schemas for every stage boundary
  config.py             # versioned config loading (metrics.yaml, policies, chain, settings)
  metrics.yaml          # monitoring contract outline + thresholds + chain config
  policies/             # labeling / pii / alert-verification policies (versioned YAML)
  mistral_client.py     # Large (extract, batch, explain) + Shieldstral (pair, policy, PII) wrapper
  timestral.py          # self-hosted Timestral backend protocol + local reference implementation
  ingest/               # any incident source -> IncidentRecord JSONL
  extract_metrics/      # Mistral Large -> extractions + merged metrics.yaml
  labeling/             # Shieldstral labeling + PII + SQLite labeling store
  dataset/              # aligned series, detection/forecasting sets, incident splits
  tuning/               # forecasting / embedding / zero-shot detectors, training, evaluation
  serve/chain.py        # LIVE CHAIN: timstral -> shieldstral -> large (printed explanation)
  serve/watcher.py      # FastAPI /sentinel/watch + feedback + alerts
  feedback/             # reviews + retrain triggers
  examples/             # synthetic incident set for end-to-end runs
  tests/                # mocked-API tests incl. chain and serve tests
  cli.py                # ingest/extract-metrics/.../serve/shadow-run entry points
```
