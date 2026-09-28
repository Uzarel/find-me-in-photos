"""Session endpoints for event mode: who needs to log in, login, logout."""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, StrictStr

from app.access import (
    SESSION_COOKIE,
    SESSION_SECONDS,
    RateLimiter,
    client_key,
    codes_match,
    has_valid_session,
    issue_token,
)
from app.config import MAX_ACCESS_CODE_LENGTH, Settings
from app.responses import ApiError, envelope

MAX_FAILED_LOGINS = 20
FAILED_LOGIN_WINDOW_SECONDS = 5 * 60
COOKIE_SAME_SITE = "strict"

logger = logging.getLogger(__name__)
router = APIRouter()


class LoginRequest(BaseModel):
    code: StrictStr = Field(min_length=1, max_length=MAX_ACCESS_CODE_LENGTH)


def create_login_limiter() -> RateLimiter:
    return RateLimiter(MAX_FAILED_LOGINS, FAILED_LOGIN_WINDOW_SECONDS)


def is_secure(request: Request) -> bool:
    return request.url.scheme == "https"


@router.get("/api/session")
def get_session(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    code = settings.access_code
    return envelope({
        "auth_required": code is not None,
        "authenticated": code is None or has_valid_session(request.cookies, code,
                                                           time.time()),
        "event_name": settings.event_name,
    })


@router.post("/api/login")
def login(request: Request, payload: LoginRequest, response: Response) -> dict:
    settings: Settings = request.app.state.settings
    if settings.access_code is None:
        raise ApiError(400, "No access code is needed.")
    limiter: RateLimiter = request.app.state.login_limiter
    client = client_key(request)
    if limiter.is_exhausted(client):
        raise ApiError(429, "Too many wrong codes. Wait a few minutes and try again.")
    if not codes_match(payload.code.strip(), settings.access_code):
        limiter.allow(client)  # only failures count towards the limit
        logger.warning("Wrong access code from %s", client)
        raise ApiError(401, "That code is not correct.")
    response.set_cookie(
        SESSION_COOKIE,
        issue_token(settings.access_code, time.time()),
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite=COOKIE_SAME_SITE,
        secure=is_secure(request),
    )
    return envelope({"authenticated": True})


@router.post("/api/logout")
def logout(request: Request, response: Response) -> dict:
    """Forget the session on this device.

    Sessions are stateless, so a copy of the cookie taken beforehand stays
    valid until it expires. Changing the access code ends every session.
    """
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite=COOKIE_SAME_SITE,
                           secure=is_secure(request))
    return envelope({"authenticated": False})
