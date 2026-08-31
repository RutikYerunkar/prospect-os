"""RFC-7807-ish error responses (§21). Deliberately small: three exception
types covering "doesn't exist", "not allowed from this state", and "the
request body doesn't parse into a domain object" — not a generic error
framework. Pydantic's own validation errors are left as FastAPI's default
422 shape; these are for the business-logic cases FastAPI can't infer.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


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


def _problem_response(error: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "type": "about:blank",
            "title": error.title,
            "detail": error.detail,
            "status": error.status_code,
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _problem_response(exc)
