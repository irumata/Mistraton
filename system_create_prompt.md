# Prompt: Build the Timestral Incident Detection System

Copy everything below the line into your coding agent (or back into this chat) to generate the system.

---

Build a production system called **Sentinel-Loop** with this division of labor across three Mistral model families:

- **Mistral Large (API)**: analyzes raw incident data (logs, tickets, postmortems, chat, raw metrics) and **extracts the numeric metrics** that describe and precede each incident.
- **Shieldstral (API + self-hosted)**: policy-based **labeling** of incident windows, records, and analysis outputs; privacy filtering; verification of alerts.
- **Timestral (small, self-hosted, e.g. 5M-209M params)**: the time-series foundation model that is **fine-tuned to detect** (and predict) incidents on the extracted metric streams.

Pipeline: raw incident data → Mistral Large metric extraction → Shieldstral labeling of time windows → time-series dataset assembly → Timestral fine-tuning (forecasting-based detection and/or embedding-based classification) → self-hosted Timestral scoring live streams → feedback loop back into the dataset.

## 1. Ingestion & normalization layer

- Ingest any incident type: outages, security events, operational/process incidents.
- Accept mixed inputs: log files (JSON/CSV/plain), monitoring metric exports (CSV/JSON time series from Prometheus/Datadog/Grafana), tickets (Jira/ServiceNow export or API), postmortem docs (Markdown/PDF/DOCX), chat threads.
- Normalize into a common `IncidentRecord` schema (JSONL): `incident_id`, `source_type`, `timestamp_start`, `timestamp_end`, `raw_text`, `metric_sources`, `severity_hint`, `linked_records`.
- For every incident, timestamp-bound all sources and align them to a common time index with the metric exports.

## 2. Metric extraction stage (Mistral Large, La Plateforme API, Batch API for cost)

For each incident, Mistral Large receives the full record bundle and outputs structured JSON:

```json
{
  "incident_id": "...",
  "incident_summary": "...",
  "metric_definitions": [
    {
      "name": "p99_latency_ms",
      "source": "prometheus:api_gateway",
      "aggregation": "1m_avg",
      "expected_normal_range": [ "40", "120" ],
      "observed_bad_range": [ "800", "12000" ],
      "lead_time_minutes": 18,
      "why_relevant": "..."
    }
  ],
  "precursor_signals": [ { "metric": "error_rate", "pattern": "gradual rise 20min before", "lead_time_minutes": 20 } ],
  "timeline": [ ... ],
  "root_cause_candidates": [ ... ],
  "prevention_actions": [ ... ]
}
```

- `metric_definitions` must be derived from the actual metric exports present in the data: which series, aggregation, and window discriminate incident vs normal periods.
- `lead_time_minutes` is the measured gap between precursor onset and incident start: this defines the model's prediction window.
- Emit a machine-readable YAML `metrics.yaml` per incident, then a merged global `metrics.yaml` (the monitoring contract: series to collect, sampling, normal ranges).

## 3. Labeling stage (Shieldstral 1.0)

- **Window labeling**: using incident timestamps plus the textual evidence, assign labels to time windows of each metric stream:
  - `normal` (no incident, outside any lead window)
  - `precursor` (inside `lead_time_minutes` before incident start)
  - `incident` (between start and end)
  - `degraded` (post-incident unstable period)
- Shieldstral validates each label by evaluating the paired evidence (text window vs policy question), e.g. "Does this evidence describe the onset of the incident referenced by this window?" Consistency checks catch timestamp errors from tickets and postmortems.
- **Quality gates**: Shieldstral reviews every Mistral Large metric extraction ("is this metric actually present in the source exports?", "does this summary contain unsupported claims?"), and PII-filters all text before anything is stored for training.
- Labels and policy versions are persisted in a labeling store (SQLite or Postgres) for reproducibility.

## 4. Time-series dataset assembly

- For each metric defined in `metrics.yaml`, build aligned numerical series from the raw exports: equally spaced, missing-value-masked (Timestral supports native missing-data handling), with past-only and known-future covariates where relevant (deployments, maintenance windows, schedules).
- Produce two datasets from labeled windows:
  - **Detection set**: sliding windows labeled `normal` / `precursor` / `incident` / `degraded`.
  - **Forecasting set**: normal-condition windows only, for forecasting-based anomaly detection.
- Fixed train/val/test splits at the incident level (all windows from one incident stay in one split), with a held-out incident test set the tuner may never touch.

## 5. Timestral tuning stage (the detector)

Base: a small self-hosted Timestral checkpoint, default **5M, with 37M/72M/209M as scale-up options**; base size is a config value. Use whichever is supported: Mistral Forge fine-tuning, or open-weights fine-tuning run locally. Implement all three detection strategies behind a shared interface so they can be evaluated against each other:

1. **Forecasting-based detection (primary)**: fine-tune Timestral to forecast each metric stream on normal-condition data. At inference, detection = observed values falling outside the predicted quantile band for a sustained period (tunable band and persistence parameters). Precursor detection = deviation of specific patterns identified in labeling (`precursor_signals`) within the lead window.
2. **Embedding-based classification**: use Timestral's context representations (frozen or fine-tuned) of the labeled windows, with a lightweight downstream head (logistic regression / small MLP) to classify window states. This reuses the same labels without needing large fine-tuning runs.
3. **Zero-shot baseline**: untrained Timestral quantile-residual anomaly detection, as the control the tuned models must beat.

- Evaluation per incident and per window: precision, recall, F1 for `incident` and `precursor` detection, mean lead time achieved in minutes, false-positive rate per 1000 window-hours, and mean absolute quantile loss on normal data.
- Hard negatives: windows that look similar to precursors in raw shape but labeled `normal` (deploy-related spikes, seasonal peaks, planned maintenance), plus known-future covariates marking planned events so expected changes do not read as anomalies.

## 6. Production stage

- Self-hosted Timestral serving (it is small enough to run on CPU or a minimal GPU): FastAPI `/watch` endpoint consuming the live metric streams defined in `metrics.yaml`, emitting events with `{metric, model_confidence, window_state, lead_time_minutes}`.
- Alert verification: for each candidate event, Shieldstral re-checks the accompanying evidence (recent log lines, ticket text) against policy before an alert fires; verified alerts and reviewer confirmations/rejections flow back into the labeling store.
- Shadow mode first: the detector runs alongside existing monitoring, alerts logged not sent, until its precision on reviewed events passes a threshold.
- Retraining trigger: scheduled, on new incident data, and on drift (precision on reviewed alerts drops below threshold).

## 7. Deliverables to produce

- Monorepo skeleton: `ingest/`, `extract-metrics/` (Mistral Large client), `labeling/` (Shieldstral client + window labeler), `dataset/` (series assembly, splits), `tuning/` (Timestral fine-tuning + classifier heads + evaluation), `serve/` (FastAPI watcher), `feedback/`, and `policies/` + `metrics.yaml` config.
- Python 3.11+, typed, pydantic schemas for every stage boundary.
- Mistral La Plateforme client wrapper handling Large (Batch API), Shieldstral (prompt, pair, and policy modes), Timestral inference, retries, and cost logging.
- CLI entry points: `ingest`, `extract-metrics`, `label-windows`, `build-dataset`, `train-detection`, `train-embedding-head`, `evaluate`, `serve`, `shadow-run`.
- `docker-compose.yml` for the watcher + labeling store; Timestral serving per its local-deployment documentation.
- Tests with mocked API calls; an end-to-end test with a tiny synthetic incident set (2 synthetic metrics + 1 incident) in `examples/`.
- README with pipeline diagram, setup, and a walkthrough of the synthetic example end to end.

## 8. Constraints

- Mistral Large analyzes and extracts; Shieldstral labels, verifies, and guards; **only Timestral detects, and only Timestral is tuned**. Never swap these roles.
- All text entering the labeling/training stores passes Shieldstral PII filtering first.
- `metrics.yaml`, policies, and all thresholds live in versioned config, never hardcoded.
- Start with the smallest Timestral (5M); scale up only if the held-out incident eval says so.
- Detection must be self-hosted and run on minimal hardware; only the extraction and labeling stages call APIs.

Start by producing the repo skeleton, pydantic schemas, `metrics.yaml` outline, and the Mistral client wrapper. Then implement stages in order: ingest → extract-metrics → label-windows → build-dataset → train-detection (forecasting path) → evaluate → serve. After each stage, run the synthetic example to verify before moving on.