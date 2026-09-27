"""HTTP hardening for a local-only app.

The app has no login because it only listens on localhost. The realistic
attacker is a web page open in the same browser, so these middlewares limit
what such a page can make the app do.
"""
from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.responses import error_response

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
