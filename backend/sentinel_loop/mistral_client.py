"""Mistral La Plateforme client wrapper.

Handles:
- Mistral Large (chat + Batch API) for metric extraction,
- Shieldstral (prompt / pair / policy modes) for labeling validation,
  quality gates, PII filtering and alert verification,
- retries with exponential backoff,
- cost logging (JSONL + in-memory).

Timestral is NOT called through the API here — it is self-hosted and only
tuned/detects (see timestral.py). The `transport` hook makes every call
mockable in tests.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol

from .schemas import AlertExplanation, DetectionEvent

logger = logging.getLogger(__name__)

EXPLAIN_SYSTEM_PROMPT = """You are the alert-explanation stage of an incident-detection system (Sentinel-Loop).
A self-hosted time-series model (Timestral) flagged a metric window, and a policy
model (Shieldstral) verified the candidate alert. Your job is to explain the alert
to the on-call human in plain language.
Output STRICT JSON only, matching this schema:
{
  "summary": str,                  // one or two sentences: what fired, on which metric, when
  "why_timestral_flagged": str,    // what the detector saw (residual vs predicted band, persistence, state)
  "why_shieldstral_verified": str, // why the evidence passed the verification policy
  "likely_causes": [str],          // ranked hypotheses grounded in the provided ranges
  "recommended_actions": [str]     // concrete next steps for the on-call
}
Rules: only use facts present in the payload; expected_normal_range vs observed_bad_range
of the metric are the ground truth for "normal" vs "bad"; never invent metrics or timestamps.
"""


class Transport(Protocol):
    """Lowest-level call: (model, messages, response_format) -> raw text."""

    def __call__(
        self,
        model: str,
        messages: list[dict],
        response_format: Optional[dict] = None,
    ) -> str: ...


class MistralSdkTransport:
    """Real transport backed by the `mistralai` SDK (created lazily)."""

    def __init__(self, api_key: str):
        from mistralai import Mistral  # lazy: import only when really calling the API

        self._client = Mistral(api_key=api_key)

    def __call__(self, model: str, messages: list[dict], response_format: Optional[dict] = None) -> str:
        kwargs: dict = {"model": model, "messages": messages}
        if response_format:
            kwargs["response_format"] = response_format
        resp = self._client.chat.complete(**kwargs)
        return resp.choices[0].message.content or ""


@dataclass
class CostEntry:
    model: str
    mode: str
    input_chars: int
    output_chars: int
    est_input_tokens: int
    est_output_tokens: int
    seconds: float


@dataclass
class CostLog:
    """Append-only cost log. Token counts are estimated from chars when the
    SDK usage payload is unavailable (est. 4 chars/token)."""

    path: Optional[str] = None
    entries: list[CostEntry] = field(default_factory=list)

    def record(self, model: str, mode: str, prompt: str, output: str, seconds: float) -> CostEntry:
        entry = CostEntry(
            model=model,
            mode=mode,
            input_chars=len(prompt),
            output_chars=len(output),
            est_input_tokens=max(1, len(prompt) // 4),
            est_output_tokens=max(1, len(output) // 4),
            seconds=seconds,
        )
        self.entries.append(entry)
        if self.path:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(entry.__dict__ | {"ts": time.time()}.__str__().replace("'", '"') + "\n")
        return entry

    @property
    def total_est_tokens(self) -> int:
        return sum(e.est_input_tokens + e.est_output_tokens for e in self.entries)


class MistralClients:
    """One wrapper for Large + Shieldstral with retries and cost logging.

    `transport` may be injected in tests; when None and an API key is set, the
    real mistralai SDK is used. Without an API key, API-calling methods raise
    RuntimeError with a clear message (offline stages never call them).
    """

    def __init__(
        self,
        api_key: str = "",
        large_model: str = "mistral-large-latest",
        shieldstral_model: str = "shieldstral-latest",
        transport: Optional[Transport] = None,
        cost_log: Optional[CostLog] = None,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.large_model = large_model
        self.shieldstral_model = shieldstral_model
        self._transport = transport
        self.cost_log = cost_log or CostLog()
        self.max_retries = max_retries

    # -- internal ---------------------------------------------------------
    def _call(self, model: str, mode: str, messages: list[dict], response_format: Optional[dict] = None) -> str:
        if self._transport is None:
            if not self.api_key:
                raise RuntimeError(
                    "MISTRAL_API_KEY is not set and no transport was injected; "
                    "API stages (extract-metrics, label-windows validation, alert verification) cannot run offline."
                )
            self._transport = MistralSdkTransport(self.api_key)
        prompt = json.dumps(messages)
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            start = time.perf_counter()
            try:
                out = self._transport(model=model, messages=messages, response_format=response_format)
                self.cost_log.record(model, mode, prompt, out, time.perf_counter() - start)
                return out
            except Exception as exc:  # noqa: BLE001 - retried, then re-raised
                last_exc = exc
                logger.warning("Mistral call %s failed (attempt %d/%d): %s", mode, attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    time.sleep(min(2**attempt, 8) * 0.1)
        raise RuntimeError(f"Mistral call '{mode}' failed after {self.max_retries} attempts") from last_exc

    # -- Mistral Large: metric extraction (chat + Batch API) ---------------
    def large_extract(self, system_prompt: str, user_payload: str) -> str:
        """Single chat completion returning structured JSON."""
        return self._call(
            self.large_model,
            "large.extract",
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            response_format={"type": "json_object"},
        )

    def large_extract_batch(self, system_prompt: str, payloads: list[str]) -> list[str]:
        """Batch API for cost efficiency. Falls back to sequential chat calls
        when the batch endpoint is unavailable."""
        if self._transport is None and self.api_key:
            try:
                return self._large_extract_batch_api(system_prompt, payloads)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Batch API unavailable (%s); falling back to sequential calls", exc)
        return [self.large_extract(system_prompt, p) for p in payloads]

    def _large_extract_batch_api(self, system_prompt: str, payloads: list[str]) -> list[str]:
        from mistralai import Mistral

        client = Mistral(api_key=self.api_key)
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            for i, p in enumerate(payloads):
                fh.write(json.dumps({
                    "custom_id": str(i),
                    "body": {
                        "model": self.large_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": p},
                        ],
                        "response_format": {"type": "json_object"},
                    },
                }) + "\n")
            input_path = fh.name
        with open(input_path, "rb") as fh:
            upload = client.files.upload(file={"file_name": Path(input_path).name, "content": fh})
        job = client.batch.jobs.create(
            input_file_id=upload.id,
            model=self.large_model,
            endpoint="/v1/chat/completions",
        )
        # Poll until completion (hackathon-scale batches finish in minutes).
        for _ in range(120):
            job = client.batch.jobs.get(job_id=job.id)
            if job.status in ("SUCCESS", "FAILED", "CANCELLED"):
                break
            time.sleep(5)
        if job.status != "SUCCESS":
            raise RuntimeError(f"batch job ended with status {job.status}")
        result = client.files.download(file_id=job.output_file_id).read().decode()
        client.files.delete(file_id=upload.id)
        outputs: list[str] = [""] * len(payloads)
        for line in result.splitlines():
            row = json.loads(line)
            outputs[int(row["custom_id"])] = row["response"]["body"]["choices"][0]["message"]["content"]
        return outputs

    # -- Mistral Large: alert explanation (detection-chain stage 3) ---------
    def large_explain_alert(
        self,
        event: DetectionEvent,
        verified: Optional[bool],
        metric_spec: Optional[dict] = None,
        recent_values: Optional[list[Optional[float]]] = None,
        max_chars: int = 4000,
    ) -> AlertExplanation:
        """Explain a fired alert for the on-call human. Third stage of the live
        detection chain (after Timestral detection and Shieldstral verification).
        Returns structured AlertExplanation; on unparseable output the raw text
        is kept in `raw` and surfaced as the summary."""
        payload = {
            "alert_event": json.loads(event.model_dump_json()),
            "shieldstral_verified": verified,  # None = shadow mode (logged, not sent)
            "metric_spec": metric_spec or {},
            "recent_values_tail": [v for v in (recent_values or [])[-30:]],
        }
        user = json.dumps(payload, indent=2)
        if len(user) > max_chars:
            user = user[:max_chars] + "\n...(truncated)"
        out = self._call(
            self.large_model,
            "large.explain_alert",
            [
                {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        try:
            parsed = json.loads(out)
            return AlertExplanation(
                summary=str(parsed.get("summary", "")).strip() or out.strip()[:500],
                why_timestral_flagged=str(parsed.get("why_timestral_flagged", "")),
                why_shieldstral_verified=str(parsed.get("why_shieldstral_verified", "")),
                likely_causes=[str(c) for c in parsed.get("likely_causes", [])],
                recommended_actions=[str(a) for a in parsed.get("recommended_actions", [])],
                raw=out,
            )
        except json.JSONDecodeError:
            return AlertExplanation(summary=out.strip()[:500], raw=out)

    # -- Shieldstral: pair / policy / PII -----------------------------------
    def shieldstral_pair(self, question: str, evidence_a: str, evidence_b: str) -> bool:
        """Pair mode: judge whether two pieces of evidence answer `question`
        consistently (e.g. text window vs. incident window). Returns bool."""
        out = self._call(
            self.shieldstral_model,
            "shieldstral.pair",
            [
                {"role": "system", "content": "You are a strict policy judge. Answer with exactly YES or NO."},
                {"role": "user", "content": (
                    f"Question: {question}\n\nEvidence A:\n{evidence_a}\n\nEvidence B:\n{evidence_b}\n\n"
                    "Does the evidence consistently support YES? Answer YES or NO."
                )},
            ],
        )
        return out.strip().upper().startswith("YES")

    def shieldstral_policy(self, text: str, policy: str) -> tuple[bool, str]:
        """Policy mode: (passes, possibly-redacted text)."""
        out = self._call(
            self.shieldstral_model,
            "shieldstral.policy",
            [
                {"role": "system", "content": "You enforce a content policy. Reply JSON: {\"passes\": bool, \"redacted\": str}."},
                {"role": "user", "content": f"Policy:\n{policy}\n\nText:\n{text}"},
            ],
            response_format={"type": "json_object"},
        )
        try:
            parsed = json.loads(out)
            return bool(parsed.get("passes")), str(parsed.get("redacted", text))
        except json.JSONDecodeError:
            return False, text

    def shieldstral_pii_filter(self, text: str, policy: str) -> str:
        """PII filtering before anything is stored for training/labeling."""
        passes, redacted = self.shieldstral_policy(text, policy)
        return redacted if passes else redacted
