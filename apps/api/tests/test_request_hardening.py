"""Checkpoint I1 Phase 9 — request-id propagation, TrustedHostMiddleware,
and the opaque-in-production catch-all 500 handler.
"""

from __future__ import annotations

import uuid

import httpx

from groundwork.api.deps import get_session_factory
from groundwork.config import settings
from groundwork.main import app


async def _get_allowing_500(client, path: str):
    """Starlette's `ServerErrorMiddleware` re-raises an unhandled exception
    even after successfully sending its response ("This allows servers to
    log the error, or allows test clients to optionally raise the error
    within the test case" — see `starlette/middleware/errors.py`), and
    `httpx.ASGITransport` defaults to propagating that (`raise_app_exceptions
    =True`). The `client` fixture deliberately keeps that default everywhere
    else (an accidental exception in a route should fail its test loudly);
    these two tests are the deliberate exception — they need to see the
    already-sent 500 response body, not a raised Python exception."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers
    ) as c:
        return await c.get(path)


async def test_response_carries_an_x_request_id_header(client):
    r = await client.get("/api/health")
    assert "x-request-id" in r.headers
    assert r.headers["x-request-id"]


async def test_incoming_x_request_id_is_echoed_back(client):
    incoming = str(uuid.uuid4())
    r = await client.get("/api/health", headers={"X-Request-ID": incoming})
    assert r.headers["x-request-id"] == incoming


async def test_different_requests_get_different_request_ids(client):
    r1 = await client.get("/api/health")
    r2 = await client.get("/api/health")
    assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


async def test_error_response_includes_request_id(client):
    incoming = str(uuid.uuid4())
    r = await client.get("/api/plays/does-not-exist", headers={"X-Request-ID": incoming})
    assert r.status_code == 404
    assert r.json()["request_id"] == incoming
    assert r.headers["x-request-id"] == incoming


async def test_unhandled_exception_returns_opaque_problem_json_in_production(client, session_factory, monkeypatch):
    """An unexpected exception (not one of our typed `ApiError`s) must
    still come back as the same problem-JSON shape, opaque in production —
    never Starlette's bare-text default 500, never the raw exception
    message leaking to the browser."""
    monkeypatch.setattr(settings, "environment", "production")

    async def _broken_session_factory():
        raise RuntimeError("simulated database outage: connection string was postgresql://user:SECRETPW@host/db")

    app.dependency_overrides[get_session_factory] = _broken_session_factory
    try:
        r = await _get_allowing_500(client, "/api/plays")
        assert r.status_code == 500
        body = r.json()
        assert body["status"] == 500
        assert "title" in body and "detail" in body
        assert "SECRETPW" not in r.text
        assert "simulated database outage" not in body["detail"]
        assert body["detail"] == "an unexpected error occurred"
    finally:
        app.dependency_overrides[get_session_factory] = lambda: session_factory


async def test_unhandled_exception_is_more_detailed_outside_production(client, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")

    async def _broken_session_factory():
        raise RuntimeError("simulated failure, no secret here")

    app.dependency_overrides[get_session_factory] = _broken_session_factory
    try:
        r = await _get_allowing_500(client, "/api/plays")
        assert r.status_code == 500
        assert "simulated failure" in r.json()["detail"]
    finally:
        app.dependency_overrides[get_session_factory] = lambda: session_factory


def test_trusted_host_middleware_is_wired_from_settings():
    """`TrustedHostMiddleware`'s `allowed_hosts` is read once, at
    app-construction time (`add_middleware` in `main.py`) — this proves
    it's actually wired to `settings.trusted_hosts` (default `["*"]`,
    permissive for local dev/tests); the behavioral proof that
    `TrustedHostMiddleware` itself rejects a mismatched Host header is
    Starlette's own, exercised directly below rather than re-tested through
    our app's import-time-bound instance."""
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    instance = next((mw for mw in app.user_middleware if mw.cls is TrustedHostMiddleware), None)
    assert instance is not None
    assert instance.kwargs.get("allowed_hosts") == settings.trusted_hosts


async def test_trusted_host_middleware_rejects_unknown_host_directly():
    import httpx
    from starlette.applications import Starlette
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def _ok(request):
        return PlainTextResponse("ok")

    probe_app = Starlette(routes=[Route("/", _ok)])
    probe_app.add_middleware(TrustedHostMiddleware, allowed_hosts=["allowed.example.com"])

    transport = httpx.ASGITransport(app=probe_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://allowed.example.com") as allowed_client:
        r = await allowed_client.get("/")
        assert r.status_code == 200

    async with httpx.AsyncClient(transport=transport, base_url="http://evil.example.com") as foreign_client:
        r = await foreign_client.get("/")
        assert r.status_code == 400
