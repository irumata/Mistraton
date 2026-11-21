"""Live detection chain: Timestral -> Shieldstral -> Mistral Large (printed).

Fixed order, never swapped (same division of labor as the offline pipeline):

1. **timstral**    — self-hosted Timestral scores the window and emits a
   candidate `DetectionEvent` (the ONLY detector; the ONLY tuned model).
2. **shieldstral** — policy verification of the candidate via pair mode; this
   is the false-positive gate. Skipped in shadow mode (alerts logged, not sent).
   When verification is required but no API client is available the chain
   fails closed (candidate suppressed).
3. **large**       — Mistral Large explains the fired alert (why Timestral
   flagged it, why Shieldstral verified it, likely causes, recommended
   actions). The explanation is PRINTED to stdout/log and stored on the alert.

Candidates rejected by Shieldstral are suppressed (not fired) when
`chain.suppress_unverified` is set — that is how false positives are avoided
before any human or downstream system sees them.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from ..config import MetricsConfig
from ..mistral_client import MistralClients
from ..schemas import AlertEvent, AlertExplanation, ChainResult, ChainStage, DetectionEvent
from ..tuning.strategies import BaseDetector

logger = logging.getLogger(__name__)


def format_explanation(alert_id: str, event: DetectionEvent, explanation: AlertExplanation,
                       verified: Optional[bool]) -> str:
    """Human-readable rendering of a Large explanation — this is what gets printed."""
    lines = [
        f"=== SENTINEL-LOOP ALERT {alert_id} (Mistral Large explanation) ===",
        (f"metric={event.metric} state={event.window_state.value} confidence={event.model_confidence:.2f} "
         f"window=[{event.window_start.isoformat()}, {event.window_end.isoformat()}) "
         f"lead={event.lead_time_minutes}min verified={verified} strategy={event.strategy}"),
        f"SUMMARY: {explanation.summary}",
    ]
    if explanation.why_timestral_flagged:
        lines.append(f"WHY TIMESTRAL FLAGGED: {explanation.why_timestral_flagged}")
    if explanation.why_shieldstral_verified:
        lines.append(f"WHY SHIELDSTRAL VERIFIED: {explanation.why_shieldstral_verified}")
    if explanation.likely_causes:
        lines.append("LIKELY CAUSES:")
        lines += [f"  - {c}" for c in explanation.likely_causes]
    if explanation.recommended_actions:
        lines.append("RECOMMENDED ACTIONS:")
        lines += [f"  - {a}" for a in explanation.recommended_actions]
    lines.append("=" * 70)
    return "\n".join(lines)


class DetectionChain:
    """Runs one window through the three detection stages in fixed order."""

    def __init__(
        self,
        detector: BaseDetector,
        config: MetricsConfig,
        clients: Optional[MistralClients] = None,
        question: str = "Does this evidence support firing an alert for this metric window?",
    ):
        self.detector = detector
        self.config = config
        self.clients = clients
        self.question = question

    def run(
        self,
        metric: str,
        window: list[Optional[float]],
        history_windows: list[list[Optional[float]]],
        window_start: datetime,
        window_end: datetime,
        shadow: bool = False,
    ) -> ChainResult:
        cfg = self.config
        stages: list[ChainStage] = []

        # -- stage 1: Timestral detects --------------------------------------
        t0 = time.perf_counter()
        event = self.detector.detect(
            metric, window, history_windows, window_start=window_start, window_end=window_end,
        )
        stages.append(ChainStage(
            stage="timstral",
            model=f"timestral-{cfg.timestral.size}-{cfg.timestral.backend}",
            passed=event is not None,
            detail=(f"state={event.window_state.value} confidence={event.model_confidence:.2f}"
                    if event else "no anomaly in window"),
            seconds=time.perf_counter() - t0,
        ))
        if event is None or event.model_confidence < cfg.thresholds.alert_confidence:
            return ChainResult(event=event, stages=stages, fired=False)

        # -- stage 2: Shieldstral verifies (false-positive gate) --------------
        verified: Optional[bool] = None
        if shadow or not cfg.chain.verify_with_shieldstral:
            verified = None  # shadow mode: logged, not sent; or verification disabled
        else:
            t0 = time.perf_counter()
            if self.clients is not None:
                try:
                    verified = self.clients.shieldstral_pair(
                        self.question,
                        f"metric={metric} state={event.window_state.value} confidence={event.model_confidence:.2f}",
                        f"window=[{window_start.isoformat()}, {window_end.isoformat()}) lead={event.lead_time_minutes}min",
                    )
                    detail = "pair verdict: " + ("YES" if verified else "NO")
                except RuntimeError as exc:
                    verified = False
                    detail = f"pair call failed, fail-closed: {exc}"
            else:
                verified = False
                detail = "verification required but no Mistral API clients available, fail-closed"
            stages.append(ChainStage(
                stage="shieldstral",
                model=self.clients.shieldstral_model if self.clients else "unavailable",
                passed=bool(verified),
                detail=detail,
                seconds=time.perf_counter() - t0,
            ))
            if cfg.chain.suppress_unverified and verified is not True:
                logger.info("suppressed false-positive candidate: metric=%s state=%s confidence=%.2f",
                            metric, event.window_state.value, event.model_confidence)
                return ChainResult(event=event, stages=stages, fired=False, suppressed=True)

        # -- stage 3: Mistral Large explains (printed) -------------------------
        alert_id = uuid.uuid4().hex[:12]
        explanation: Optional[AlertExplanation] = None
        printed: Optional[str] = None
        if cfg.chain.explain_with_large and self.clients is not None:
            t0 = time.perf_counter()
            spec = cfg.metric(metric)
            try:
                explanation = self.clients.large_explain_alert(
                    event,
                    verified,
                    metric_spec=spec.model_dump() if spec else None,
                    recent_values=window,
                    max_chars=cfg.chain.explanation_max_chars,
                )
                stages.append(ChainStage(
                    stage="large",
                    model=self.clients.large_model,
                    passed=True,
                    detail=f"explanation summary: {explanation.summary[:120]}",
                    seconds=time.perf_counter() - t0,
                ))
                printed = format_explanation(alert_id, event, explanation, verified)
                print(printed)  # the explanation is printed, not just returned
                logger.info("alert %s explained by %s", alert_id, self.clients.large_model)
            except RuntimeError as exc:
                stages.append(ChainStage(
                    stage="large", model=self.clients.large_model, passed=False,
                    detail=f"explanation call failed: {exc}", seconds=time.perf_counter() - t0,
                ))
                logger.warning("Large explanation failed for alert %s: %s", alert_id, exc)

        alert = AlertEvent(
            alert_id=alert_id, event=event, verified=verified, explanation=printed,
        )
        return ChainResult(event=event, alert=alert, explanation=explanation, stages=stages, fired=True)
