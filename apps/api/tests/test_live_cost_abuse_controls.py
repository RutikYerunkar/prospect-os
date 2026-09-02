"""Checkpoint I1 Phase 8B — Live cost/abuse controls.

Covers: LIVE_MAX_ACTIVE_RUNS, LIVE_DAILY_RUN_ALLOWANCE (both DB-backed, no
Redis, no in-process counter), the public write/preview rate limiters, and
the request body size cap.
"""

from __future__ import annotations

import json

import pytest

from groundwork.api.routers import operator as operator_router
from groundwork.api.routers import plays as plays_router
from groundwork.config import settings
from groundwork.models.enums import Mode
from tests.api_helpers import create_play, login_as_operator, start_run


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """These limiters are deliberately process-local module-level
    singletons (Phase 8B) — reset between tests so one test's requests
    (and any `_max_attempts` override a test applies directly, since that
    field is baked in at construction and can't be reached via
    `monkeypatch.setattr(settings, ...)` after the fact) don't leak into
    another test's budget."""
    limiters = [operator_router._login_limiter, plays_router._write_limiter, plays_router._preview_limiter]
    originals = [(lim, lim._max_attempts) for lim in limiters]
    for lim in limiters:
        lim._hits.clear()
    yield
    for lim, max_attempts in originals:
        lim._max_attempts = max_attempts
        lim._hits.clear()


async def test_live_max_active_runs_blocks_a_second_concurrent_live_run(client, session_factory, monkeypatch):
    await login_as_operator(client, monkeypatch)
    monkeypatch.setattr(settings, "live_max_active_runs", 1)

    from groundwork.repositories.plays import PlayRepository
    from groundwork.repositories.runs import RunRepository

    plays = PlayRepository(session_factory)
    runs = RunRepository(session_factory)
    play_id = await plays.create(name="x", objective_text="x", icp_spec={}, mode="live")
    await runs.create(play_id=play_id, mode="live", seed=1)  # already RUNNING

    demo_play = await create_play(client)
    r = await client.post(f"/api/plays/{demo_play['id']}/runs", json={"mode": "live"})
    assert r.status_code == 429
    assert "LIVE_MAX_ACTIVE_RUNS" in r.json()["detail"]


async def test_live_max_active_runs_allows_a_run_when_under_the_limit(client, session_factory, monkeypatch):
    await login_as_operator(client, monkeypatch)
    monkeypatch.setattr(settings, "live_max_active_runs", 5)

    demo_play = await create_play(client)
    r = await client.post(f"/api/plays/{demo_play['id']}/runs", json={"mode": "live"})
    # No OPENAI_API_KEY configured in tests -> 422 from `_require_live_runtime`,
    # but crucially NOT 429 — the active-run gate itself passed.
    assert r.status_code == 422


async def test_live_daily_run_allowance_blocks_once_reached(client, session_factory, monkeypatch):
    await login_as_operator(client, monkeypatch)
    monkeypatch.setattr(settings, "live_max_active_runs", 999)
    monkeypatch.setattr(settings, "live_daily_run_allowance", 2)

    from groundwork.repositories.plays import PlayRepository
    from groundwork.repositories.runs import RunRepository
    from groundwork.timeutil import utcnow

    plays = PlayRepository(session_factory)
    runs = RunRepository(session_factory)
    play_id = await plays.create(name="x", objective_text="x", icp_spec={}, mode="live")
    # Two already-finished (not active) live runs started within the last
    # 24h — the daily allowance counts these too, not just RUNNING ones.
    for _ in range(2):
        run_id = await runs.create(play_id=play_id, mode="live", seed=1)
        await runs.finalize(run_id, status="COMPLETED", counters={})

    demo_play = await create_play(client)
    r = await client.post(f"/api/plays/{demo_play['id']}/runs", json={"mode": "live"})
    assert r.status_code == 429
    assert "LIVE_DAILY_RUN_ALLOWANCE" in r.json()["detail"]


async def test_public_write_rate_limit_on_create_play(client):
    # `_max_attempts` is baked into the limiter at construction (see
    # `_reset_rate_limiters` above for why `monkeypatch.setattr(settings,
    # ...)` alone wouldn't reach it) — set it directly for this test.
    plays_router._write_limiter._max_attempts = 3

    for _ in range(3):
        r = await client.post("/api/plays", json={"objective": "x"})
        assert r.status_code == 201

    r = await client.post("/api/plays", json={"objective": "x"})
    assert r.status_code == 429


async def test_preview_rate_limit(client, monkeypatch):
    plays_router._preview_limiter._max_attempts = 3

    for _ in range(3):
        r = await client.post("/api/plays/preview", json={"objective": "x"})
        assert r.status_code == 200

    r = await client.post("/api/plays/preview", json={"objective": "x"})
    assert r.status_code == 429


async def test_oversized_objective_is_rejected_not_truncated(client):
    """`MaxBodySizeMiddleware` reads `settings.max_request_body_bytes` once
    at app-construction time (it's real ASGI middleware, not a per-request
    dependency) — this test exercises the actual configured default rather
    than trying to patch an already-built middleware instance. A body this
    oversized is rejected by Pydantic's own `objective` `max_length=2000`
    first either way; both layers exist and either one rejecting (never a
    silent truncation or a 500) is the property under test."""
    big_objective = "x" * 5000
    r = await client.post("/api/plays/preview", json={"objective": big_objective})
    assert r.status_code in (413, 422)


async def test_body_size_middleware_rejects_declared_oversized_content_length(client):
    """Direct test of `MaxBodySizeMiddleware` itself against the real
    configured `max_request_body_bytes`, via a `Content-Length` large
    enough to exceed even that generous default — independent of whatever
    Pydantic field limits also happen to apply to this particular route."""
    huge_payload = json.dumps({"objective": "x" * (settings.max_request_body_bytes + 1000)})
    r = await client.post(
        "/api/plays/preview",
        content=huge_payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413


async def test_normal_sized_request_body_is_not_rejected(client):
    r = await client.post("/api/plays/preview", json={"objective": "a reasonably sized objective"})
    assert r.status_code == 200
