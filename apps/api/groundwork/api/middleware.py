"""ASGI middleware — Checkpoint I1 Phases 8B/9.

Kept as plain ASGI callables (not Starlette `BaseHTTPMiddleware` subclasses)
where a raw `Content-Length` check is all that's needed — no reason to
buffer/re-stream the body through Starlette's `BaseHTTPMiddleware` adapter
just to reject an oversized request before it's even parsed.
"""

from __future__ import annotations

import uuid

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

REQUEST_ID_HEADER = b"x-request-id"


class MaxBodySizeMiddleware:
    """Rejects a request outright (413) when its `Content-Length` header
    exceeds `max_body_size` — before routing, before Pydantic validation,
    before a single byte of the body is read into memory.

    Caveat, stated plainly rather than silently: this checks the declared
    `Content-Length` header, not bytes actually received. A client that
    lies about `Content-Length` or sends a chunked-transfer body without
    one bypasses this specific check — Pydantic's own field-level limits
    (`objective`'s `max_length=2000`, etc.) are the second layer for that
    case. A byte-accurate streaming cap would need to wrap `receive()` and
    abort mid-stream, which is real complexity for a threat this API's
    actual size (no file uploads, no unbounded fields) doesn't call for.
    """

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    content_length = int(value)
                except ValueError:
                    content_length = None
                if content_length is not None and content_length > self.max_body_size:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "type": "about:blank",
                            "title": "Request Entity Too Large",
                            "detail": f"request body exceeds the {self.max_body_size}-byte limit",
                            "status": 413,
                        },
                    )
                    await response(scope, receive, send)
                    return
                break

        await self.app(scope, receive, send)


class RequestIdMiddleware:
    """Assigns/propagates an `X-Request-ID` per request — a plain ASGI
    middleware (not `BaseHTTPMiddleware`) specifically so it never buffers
    or otherwise interferes with the SSE `StreamingResponse` at
    `GET /api/runs/{id}/events` (a documented `BaseHTTPMiddleware` caveat
    with real streaming responses).

    An incoming `X-Request-ID` is trusted and echoed back as-is — this is a
    correlation id for log/response tracing, never an authorization or
    trust decision, so accepting a caller-supplied value is fine (and
    useful: a frontend or load balancer that already minted one keeps it
    across the hop). `request.state.request_id` is what
    `api/errors.py`'s handlers read to include it in error responses/logs.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = None
        for name, value in scope.get("headers") or []:
            if name == REQUEST_ID_HEADER:
                request_id = value.decode("latin-1")
                break
        if not request_id:
            request_id = str(uuid.uuid4())

        scope["state"] = {**scope.get("state", {}), "request_id": request_id}

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.append("X-Request-ID", request_id)
            await send(message)

        await self.app(scope, receive, send_with_request_id)
