"""HTTP hardening for both modes.

Local mode has no login because the app only listens on localhost; there the
realistic attacker is a web page open in the same browser. Event mode is
reachable by strangers, so it requires a session and rate-limits requests.
"""
from __future__ import annotations

import time
from collections.abc import Mapping

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.access import RateLimiter, client_key, has_valid_session
from app.responses import error_response

# Everything not listed here needs a session when an access code is set.
OPEN_PATHS = frozenset({
    "/", "/api/health", "/api/session", "/api/login", "/api/logout", "/favicon.ico",
})
OPEN_PREFIXES = ("/static/",)
LOGIN_REQUIRED = "Enter the access code to continue."
TOO_MANY_REQUESTS = "Too many requests. Wait a minute and try again."

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' blob:; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}
BODY_TOO_LARGE = "The request is too large."
INVALID_LENGTH = "Invalid Content-Length header."


class SecurityHeadersMiddleware:
    """Adds the security headers to every HTTP response."""

    def __init__(self, app: ASGIApp):
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self._app(scope, receive, send_with_headers)


def is_open_path(path: str) -> bool:
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)


class AccessMiddleware:
    """Requires a session in event mode and rate-limits the costly endpoints.

    Runs before the request body is parsed, so strangers cannot make the app
    buffer uploads, and denies by default so new routes start out protected.
    """

    def __init__(self, app: ASGIApp, access_code: str | None,
                 limiters: Mapping[str, RateLimiter]):
        self._app = app
        self._access_code = access_code
        self._limiters = dict(limiters)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        path = scope["path"]
        if self._needs_login(connection, path):
            await error_response(401, LOGIN_REQUIRED)(scope, receive, send)
            return
        limiter = self._limiters.get(path) if scope["method"] == "POST" else None
        if limiter is not None and not limiter.allow(client_key(connection)):
            await error_response(429, TOO_MANY_REQUESTS)(scope, receive, send)
            return
        await self._app(scope, receive, send)

    def _needs_login(self, connection: HTTPConnection, path: str) -> bool:
        if self._access_code is None or is_open_path(path):
            return False
        return not has_valid_session(connection.cookies, self._access_code, time.time())


class BodyLimitMiddleware:
    """Rejects oversized request bodies before they are parsed or buffered."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and not declared.isdigit():
            await error_response(400, INVALID_LENGTH)(scope, receive, send)
            return
        if declared is not None and int(declared) > self._max_bytes:
            await error_response(413, BODY_TOO_LARGE)(scope, receive, send)
            return
        await self._app(scope, self._counting(receive), send)

    def _counting(self, receive: Receive) -> Receive:
        """Wrap receive so bodies without a declared length are limited too."""
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    raise HTTPException(413, BODY_TOO_LARGE)
            return message

        return limited_receive
