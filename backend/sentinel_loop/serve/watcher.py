"""FastAPI watcher: self-hosted Timestral scoring live metric streams.

POST /watch consumes live streams defined in metrics.yaml and emits events
{metric, model_confidence, window_state, lead_time_minutes}. Candidate alerts
are re-verified by Shieldstral against policy before firing; in shadow mode
alerts are logged, not sent. Reviewer confirmations/rejections flow back into
the labeling store (feedback loop).
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..config import (MetricsConfig, Settings, artifacts_dir_for, db_path_for,
                      load_policy, resolve_config)
from ..feedback.loop import check_drift_and_trigger, record_review
from ..labeling.store import LabelingStore
from ..mistral_client import MistralClients
from ..schemas import AlertEvent, DetectionEvent, WindowState
from ..tuning.strategies import BaseDetector, load_detector

logger = logging.getLogger(__name__)


class StreamPoint(BaseModel):
    ts: datetime
    value: Optional[float] = None


class WatchRequest(BaseModel):
    streams: dict[str, list[StreamPoint]]
    shadow: bool = False  # shadow mode: alerts logged, not sent


class WatchResponse(BaseModel):
    events: list[DetectionEvent]
    alerts: list[AlertEvent]
    shadow: bool


class FeedbackRequest(BaseModel):
    alert_id: str
    decision: str = Field(pattern="^(confirmed|rejected)$")
    reviewer: str = ""


class _StreamState:
    """In-memory ring buffer of recent points + last emitted window per metric."""

    def __init__(self, max_points: int = 100000):
        self.points: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_points))
        self.last_emitted: dict[str, datetime] = {}


def create_sentinel_router(
    detector: Optional[BaseDetector] = None,
    config: Optional[MetricsConfig] = None,
    store: Optional[LabelingStore] = None,
    clients: Optional[MistralClients] = None,
    settings: Optional[Settings] = None,
):
    from fastapi import APIRouter

    router = APIRouter()
    settings = settings or Settings()
    state = _StreamState()
    _detector = detector
    _config = config
    _store = store
    _clients = clients

    def get_store() -> LabelingStore:
        nonlocal _store
        if _store is None:
            _store = LabelingStore(db_path_for(settings))
        return _store

    def get_detector() -> BaseDetector:
        nonlocal _detector
        if _detector is None:
            art = Path(artifacts_dir_for(settings)) / "detector_forecasting.json"
            if not art.exists():
                raise HTTPException(503, f"no trained detector at {art}; run train-detection first")
            _detector = load_detector(art)
        return _detector

    def get_config() -> MetricsConfig:
        nonlocal _config
        if _config is None:
            cfg, _ = resolve_config(settings.data_dir, None)
            _config = cfg
        return _config

    def get_clients() -> Optional[MistralClients]:
        nonlocal _clients
        if _clients is None and settings.mistral_api_key:
            _clients = MistralClients(api_key=settings.mistral_api_key,
                                      large_model=settings.large_model,
                                      shieldstral_model=settings.shieldstral_model)
        return _clients

    @router.get("/health")
    def health():
        return {"status": "ok", "service": "sentinel-loop"}

    @router.post("/watch", response_model=WatchResponse)
    def watch(req: WatchRequest):
        det = get_detector()
        cfg = get_config()
        wlen = timedelta(seconds=cfg.window.length_seconds)
        step = timedelta(seconds=cfg.window.step_seconds)
        events: list[DetectionEvent] = []
        alerts: list[AlertEvent] = []
        policy = load_policy(cfg.policies_dir, "alert_verification_policy.yaml")
        question = policy.get("pair_question", "Does this evidence support firing an alert?")
        cl = get_clients()

        for metric, points in req.streams.items():
            spec = cfg.metric(metric)
            if spec is None:
                continue
            buf = state.points[metric]
            for p in points:
                buf.append((p.ts, p.value))
            series = sorted(buf, key=lambda x: x[0])
            if len(series) < 2:
                continue
            n = max(1, cfg.window.length_seconds // spec.sampling_seconds)
            # slide windows over the stream: full buffer on first sight of a
            # metric, only new windows afterwards (dedup via last_emitted)
            end_idx = len(series)
            starts = []
            i = end_idx - n
            step_idx = max(1, cfg.window.step_seconds // spec.sampling_seconds)
            while i >= 0 and (metric not in state.last_emitted or series[i][0] > state.last_emitted[metric]):
                starts.append(i)
                i -= step_idx
            history: list[list] = []
            for s_idx in reversed(starts):
                vals = [v for _, v in series[s_idx:s_idx + n]]
                ws = series[s_idx][0]
                we = ws + wlen
                if ws <= state.last_emitted.get(metric, datetime.min.replace(tzinfo=timezone.utc)):
                    history.append(vals)
                    continue
                ev = det.detect(metric, vals, history, window_start=ws, window_end=we)
                history.append(vals)
                if ev is None:
                    continue
                state.last_emitted[metric] = ws
                events.append(ev)
                if ev.model_confidence >= cfg.thresholds.alert_confidence:
                    verified: Optional[bool] = None
                    if req.shadow:
                        verified = None  # shadow: logged, not sent
                    elif cl is not None:
                        try:
                            verified = cl.shieldstral_pair(
                                question,
                                f"metric={metric} state={ev.window_state.value} confidence={ev.model_confidence:.2f}",
                                f"window=[{ws.isoformat()}, {we.isoformat()}) lead={ev.lead_time_minutes}min",
                            )
                        except RuntimeError:
                            verified = False
                    else:
                        verified = False
                    alert = AlertEvent(
                        alert_id=uuid.uuid4().hex[:12], event=ev,
                        verified=verified if not req.shadow else None,
                    )
                    get_store().save_alert(alert)
                    alerts.append(alert)
        return WatchResponse(events=events, alerts=alerts, shadow=req.shadow)

    @router.post("/feedback")
    def feedback(req: FeedbackRequest):
        alert = record_review(get_store(), req.alert_id, req.decision, req.reviewer)
        if alert is None:
            raise HTTPException(404, f"alert {req.alert_id} not found")
        drift = check_drift_and_trigger(get_store(), get_config())
        return {"alert": alert.alert_id, "verified": alert.verified, "drift_trigger": drift}

    @router.get("/alerts")
    def list_alerts(limit: int = 100):
        return {"alerts": [a.model_dump(mode="json") for a in get_store().list_alerts(limit)]}

    return router


def create_app(**kwargs) -> FastAPI:
    app = FastAPI(title="Sentinel-Loop watcher", docs_url="/docs", redoc_url=None,
                  openapi_url="/openapi.json")
    app.include_router(create_sentinel_router(**kwargs), prefix="/sentinel")
    return app


# module-level app for `uvicorn sentinel_loop.serve.watcher:app`
app = create_app()
