"""Plug-and-play voice output via Mistral Voxtral TTS.

Set MISTRAL_API_KEY in the API container environment to enable; optional
VOICE_MODEL (default voxtral-mini-tts-3-0) and VOICE_DEFAULT (a preset or
cloned voice_id, e.g. the "Bea Cribella" voice) configure the default voice.

GET  /status  -> capability report (always 200, enabled flag included)
POST /say     -> {text, voice_id?, model?} -> audio/mpeg bytes

The SDK client is created lazily per request and can be injected in tests
via client_factory. The endpoint is public and metered; text is capped at
4000 characters.
"""

import base64
import logging
import os
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

DEFAULT_MODEL = "voxtral-mini-tts-3-0"
MAX_TEXT_CHARS = 4000


class SayRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    voice_id: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=100)


def _api_key() -> str:
    return os.environ.get("MISTRAL_API_KEY", "")


def _default_model() -> str:
    return os.environ.get("VOICE_MODEL", DEFAULT_MODEL)


def _default_voice_id() -> str:
    return os.environ.get("VOICE_DEFAULT", "")


def _sdk_client(api_key: str):
    from mistralai import Mistral

    return Mistral(api_key=api_key)


def _extract_audio(response) -> bytes:
    data = getattr(response, "audio_data", None)
    if isinstance(data, str) and data:
        return base64.b64decode(data)
    raw = getattr(response, "data", None)
    if isinstance(raw, str) and raw:
        return base64.b64decode(raw)
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(response, (bytes, bytearray)):
        return bytes(response)
    raise RuntimeError("Unexpected TTS response payload")


def create_voice_router(client_factory: Optional[Callable[[str], object]] = None) -> APIRouter:
    router = APIRouter()
    factory = client_factory or _sdk_client

    @router.get("/status")
    def status():
        return {
            "status": "ok",
            "voice": {
                "enabled": bool(_api_key()),
                "model": _default_model(),
                "voice_id": _default_voice_id() or None,
            },
        }

    @router.post("/say")
    def say(payload: SayRequest):
        api_key = _api_key()
        if not api_key:
            raise HTTPException(503, "Voice is not configured (MISTRAL_API_KEY is not set)")
        kwargs = {
            "model": payload.model or _default_model(),
            "input": payload.text,
            "response_format": "mp3",
        }
        voice_id = payload.voice_id or _default_voice_id()
        if voice_id:
            kwargs["voice_id"] = voice_id
        try:
            response = factory(api_key).audio.speech.complete(**kwargs)
            audio = _extract_audio(response)
        except Exception:
            log.exception("Voxtral TTS request failed")
            raise HTTPException(502, "Voice synthesis failed") from None
        return Response(content=audio, media_type="audio/mpeg")

    return router
