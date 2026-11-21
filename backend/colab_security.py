"""Mandatory authentication for the entire Colab ASGI application."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import secrets
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def web_auth_settings() -> dict:
    """Fail closed at startup; an old API key never enables anonymous access."""
    username = os.environ.get("SENTINEL_WEB_USERNAME", "safestral")
    password = os.environ.get("SENTINEL_WEB_PASSWORD", "")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", username):
        raise RuntimeError("Set SENTINEL_WEB_USERNAME to a valid login name.")
    if len(password) < 32 or len(password) > 512 or any(c in password for c in "\r\n\x00"):
        raise RuntimeError("Set SENTINEL_WEB_PASSWORD to a strong password of at least 32 characters.")
    public_origin = os.environ.get("SENTINEL_PUBLIC_URL", "").rstrip("/")
    if public_origin:
        url = urlsplit(public_origin)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.path or url.query or url.fragment):
            raise RuntimeError("SENTINEL_PUBLIC_URL must be an HTTPS origin without a path.")
    return {"username": username, "password": password, "public_origin": public_origin}


class ColabAuthMiddleware:
    """Guard HTTP, mounted files, docs and WebSocket upgrades before routing.

    No URL/header is an authentication bypass. In particular, caller-supplied
    Cloudflare identity headers and the legacy X-API-Key are not credentials.
    """

    def __init__(self, app: ASGIApp, username: str, password: str, public_origin: str = ""):
        self.app = app
        self.expected = hashlib.sha256(f"{username}:{password}".encode("utf-8")).digest()
        self.public_origin = public_origin

    def authenticated(self, headers: Headers) -> bool:
        values = headers.getlist("authorization")
        if len(values) != 1 or len(values[0]) > 4096:
            return False
        scheme, separator, encoded = values[0].partition(" ")
        if not separator or scheme.lower() != "basic":
            return False
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return False
        return secrets.compare_digest(hashlib.sha256(decoded).digest(), self.expected)

    def same_origin(self, scope: Scope, headers: Headers) -> bool:
        # Browsers must not replay cached Basic credentials on cross-site writes.
        if headers.get("sec-fetch-site") == "cross-site":
            return False
        origin = headers.get("origin")
        if origin is None:  # authenticated CLI clients do not send Origin
            return True
        scheme = {"ws": "http", "wss": "https"}.get(scope["scheme"], scope["scheme"])
        local_origin = f"{scheme}://{headers.get('host', '')}"
        allowed = {local_origin}
        if self.public_origin:
            allowed.add(self.public_origin)
        return origin in allowed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        authenticated = self.authenticated(headers)
        unsafe = scope["type"] == "websocket" or scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
        origin_ok = not unsafe or self.same_origin(scope, headers)

        if scope["type"] == "websocket" and (not authenticated or not origin_ok):
            await send({"type": "websocket.close", "code": 1008})
            return

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["Cache-Control"] = "private, no-store"
                response_headers["Pragma"] = "no-cache"
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["X-Frame-Options"] = "DENY"
                response_headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        if not authenticated:
            response = JSONResponse(
                {"detail": "Authentication required"}, status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Safestral", charset="UTF-8"'},
            )
            await response(scope, receive, private_send)
            return
        if not origin_ok:
            response = JSONResponse({"detail": "Cross-site request denied"}, status_code=403)
            await response(scope, receive, private_send)
            return
        await self.app(scope, receive, private_send)
