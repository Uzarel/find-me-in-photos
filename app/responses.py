"""The response envelope shared by every API endpoint."""
from __future__ import annotations

from fastapi.responses import JSONResponse


def envelope(data=None, error: str | None = None) -> dict:
    return {"success": error is None, "data": data, "error": error}


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(envelope(error=message), status_code=status_code)
