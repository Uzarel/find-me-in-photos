"""Event mode building blocks: access code checks, session tokens, rate limits.

Sessions are stateless. A token is an expiry time signed with a key derived
from the access code, so changing the code ends every session.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import logging
import threading
import time
from collections.abc import Callable, Mapping

from starlette.requests import HTTPConnection

logger = logging.getLogger(__name__)

UNKNOWN_CLIENT = "unknown"
SESSION_COOKIE = "ff_session"
SESSION_SECONDS = 12 * 60 * 60
KEY_CONTEXT = b"face-finder-session:"
KEY_ITERATIONS = 200_000
KEY_CACHE_SIZE = 8
TOKEN_SEPARATOR = "."
MAX_TOKEN_LENGTH = 128
MAX_TRACKED_KEYS = 10_000


def codes_match(given: str, expected: str) -> bool:
    """Compare in constant time so response timing reveals nothing."""
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


@functools.lru_cache(maxsize=KEY_CACHE_SIZE)
def session_key(access_code: str) -> bytes:
    """Signing key for session tokens, derived once per access code.

    The derivation is deliberately slow, so someone holding a captured token
    cannot quickly try candidate codes against it.
    """
    return hashlib.pbkdf2_hmac("sha256", access_code.encode("utf-8"), KEY_CONTEXT,
                               KEY_ITERATIONS)


def _sign(access_code: str, expiry: int) -> str:
    key = session_key(access_code)
    return hmac.new(key, str(expiry).encode("ascii"), hashlib.sha256).hexdigest()


def issue_token(access_code: str, now: float) -> str:
    expiry = int(now) + SESSION_SECONDS
    return f"{expiry}{TOKEN_SEPARATOR}{_sign(access_code, expiry)}"


def is_valid_token(token: str, access_code: str, now: float) -> bool:
    if len(token) > MAX_TOKEN_LENGTH:
        return False
    expiry_text, separator, signature = token.partition(TOKEN_SEPARATOR)
    if not separator or not (expiry_text.isascii() and expiry_text.isdigit()):
        return False
    expiry = int(expiry_text)
    expected = _sign(access_code, expiry)
    if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("ascii")):
        return False
    return now < expiry


def has_valid_session(cookies: Mapping[str, str], access_code: str, now: float) -> bool:
    token = cookies.get(SESSION_COOKIE)
    return token is not None and is_valid_token(token, access_code, now)


def client_key(connection: HTTPConnection) -> str:
    """The address rate limits are counted against."""
    if connection.client is None:
        logger.warning("Request without a client address: it shares one rate limit "
                       "with every other such request")
        return UNKNOWN_CLIENT
    return connection.client.host


class RateLimiter:
    """Sliding-window limiter, kept in memory and safe to share between threads."""

    def __init__(self, limit: int, window_seconds: float,
                 clock: Callable[[], float] = time.monotonic,
                 max_keys: int = MAX_TRACKED_KEYS):
        if limit <= 0 or window_seconds <= 0 or max_keys <= 0:
            raise ValueError("limit, window_seconds and max_keys must be positive")
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[float, ...]] = {}

    def _with_room_for(self, key: str,
                       active: dict[str, tuple[float, ...]]) -> dict[str, tuple[float, ...]]:
        """Drop the least recently seen addresses so memory stays bounded."""
        if key in active or len(active) < self._max_keys:
            return active
        newest_first = sorted(active, key=lambda name: active[name][-1], reverse=True)
        return {name: active[name] for name in newest_first[:self._max_keys - 1]}

    def _active_hits(self, now: float) -> dict[str, tuple[float, ...]]:
        cutoff = now - self._window
        return {
            key: recent
            for key, hits in self._hits.items()
            if (recent := tuple(hit for hit in hits if hit > cutoff))
        }

    def allow(self, key: str) -> bool:
        """Record an attempt and tell whether it is within the limit."""
        now = self._clock()
        with self._lock:
            active = self._active_hits(now)
            mine = active.get(key, ())
            allowed = len(mine) < self._limit
            if allowed:
                active = {**self._with_room_for(key, active), key: (*mine, now)}
            self._hits = active
        return allowed

    def is_exhausted(self, key: str) -> bool:
        """Tell whether the limit is reached, without recording an attempt."""
        now = self._clock()
        with self._lock:
            self._hits = self._active_hits(now)
            return len(self._hits.get(key, ())) >= self._limit

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._hits)
