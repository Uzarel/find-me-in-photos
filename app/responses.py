"""The response envelope and error type shared by every API endpoint."""
from __future__ import annotations

from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def envelope(data=None, error: str | None = None) -> dict:
    return {"success": error is None, "data": data, "error": error}


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(envelope(error=message), status_code=status_code)
