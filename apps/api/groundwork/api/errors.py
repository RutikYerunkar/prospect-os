"""RFC-7807-ish error responses (§21). Deliberately small: three exception
types covering "doesn't exist", "not allowed from this state", and "the
request body doesn't parse into a domain object" — not a generic error
framework. Pydantic's own validation errors are left as FastAPI's default
422 shape; these are for the business-logic cases FastAPI can't infer.

Checkpoint I1 Phase 9 added: `UnauthorizedError`/`ForbiddenError`/
`TooManyRequestsError` (Phase 8/8B), and a catch-all handler for anything
NOT one of these typed errors — an unhandled application bug must still
produce the same problem-JSON shape (never Starlette's bare-text default),
log its traceback server-side (redacted first), and never hand the browser
raw exception text in production.
"""

from __future__ import annotations

import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from groundwork.config import settings
from groundwork.observability.redact import redact

logger = logging.getLogger(__name__)


class ApiError(Exception):
    status_code: int = 500
    title: str = "Internal Server Error"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class NotFoundError(ApiError):
    status_code = 404
    title = "Not Found"


class ConflictError(ApiError):
    status_code = 409
    title = "Conflict"


class UnprocessableEntityError(ApiError):
    status_code = 422
    title = "Unprocessable Entity"


class UnauthorizedError(ApiError):
    """No/invalid operator session, or Live requested without one
    (Checkpoint I1 Phase 8) — never carries any detail derived from the
    submitted passphrase or cookie."""

    status_code = 401
    title = "Unauthorized"


class ForbiddenError(ApiError):
    """A valid request that's structurally not allowed — e.g. an unsafe
    method whose `Origin` header doesn't match a configured allowed origin
    (Phase 8's CSRF guard)."""

    status_code = 403
    title = "Forbidden"


class TooManyRequestsError(ApiError):
    status_code = 429
    title = "Too Many Requests"


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _problem_response(error: ApiError, request_id: str | None) -> JSONResponse:
    content = {
        "type": "about:blank",
        "title": error.title,
        "detail": error.detail,
        "status": error.status_code,
    }
    if request_id:
        content["request_id"] = request_id
    return JSONResponse(status_code=error.status_code, content=content)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _problem_response(exc, _request_id(request))

    @app.exception_handler(Exception)
    async def _handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        """Anything that reaches here is a genuine application bug (every
        expected error path raises `ApiError` or lets FastAPI's own
        `RequestValidationError`/`HTTPException` handling take it — neither
        of those reaches this handler). Never expose the raw exception to
        the browser in production; always log the (redacted) traceback
        server-side regardless of environment, so it's still debuggable."""
        request_id = _request_id(request)
        redacted_traceback = redact(traceback.format_exc())
        logger.error(
            "unhandled exception on %s %s\n%s",
            request.method, request.url.path, redacted_traceback,
            extra={"request_id": request_id},
        )
        detail = (
            "an unexpected error occurred"
            if settings.environment == "production"
            else (redact(str(exc)) or "an unexpected error occurred")
        )
        content = {"type": "about:blank", "title": "Internal Server Error", "detail": detail, "status": 500}
        if request_id:
            content["request_id"] = request_id
        return JSONResponse(status_code=500, content=content)
