"""Checkpoint I1 Phase 8 — operator session + Live gate.

Covers: login/logout mechanics, cookie flags, constant-time passphrase
comparison, rate-limited failed attempts, the Origin/CSRF guard, and that
`mode="live"` alone grants nothing across every Live-touching endpoint.
"""

from __future__ import annotations

import pytest

from groundwork.api.routers import operator as operator_router
from groundwork.config import settings
from tests.api_helpers import (
    TEST_OPERATOR_PASSPHRASE,
    TEST_SESSION_SIGNING_KEY,
    create_play,
    login_as_operator,
    start_run,
)


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter():
    """The login rate limiter is a deliberately process-local, module-level
    singleton (Checkpoint I1 Phase 8/8B — see `api/rate_limit.py`'s
    docstring on why it's process-local at all), so without this it
    accumulates failed-attempt state across every test in this file (and
    beyond) that shares a client key, not just within one test."""
    operator_router._login_limiter._hits.clear()
    yield
    operator_router._login_limiter._hits.clear()


async def test_login_fails_when_operator_not_configured(client) -> None:
    # Default test settings: OPERATOR_PASSPHRASE/SESSION_SIGNING_KEY unset.
    r = await client.post("/api/operator/session", json={"passphrase": "anything"})
    assert r.status_code == 401


async def test_login_succeeds_with_correct_passphrase_and_sets_cookie(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "operator_passphrase", TEST_OPERATOR_PASSPHRASE)
    monkeypatch.setattr(settings, "session_signing_key", TEST_SESSION_SIGNING_KEY)

    r = await client.post("/api/operator/session", json={"passphrase": TEST_OPERATOR_PASSPHRASE})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    set_cookie = r.headers.get("set-cookie", "")
    assert "groundwork_operator_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Domain=" not in set_cookie  # host-only cookie
    assert "Max-Age=" in set_cookie
    # Never Secure in the default (non-production) test environment — a
    # Secure cookie over plain-HTTP local dev simply wouldn't be sent.
    assert "Secure" not in set_cookie
    # The passphrase itself never appears in the response body or headers.
    assert TEST_OPERATOR_PASSPHRASE not in r.text
    for value in r.headers.values():
        assert TEST_OPERATOR_PASSPHRASE not in value


async def test_login_cookie_is_secure_in_production(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "operator_passphrase", TEST_OPERATOR_PASSPHRASE)
    monkeypatch.setattr(settings, "session_signing_key", TEST_SESSION_SIGNING_KEY)
    monkeypatch.setattr(settings, "environment", "production")

    r = await client.post("/api/operator/session", json={"passphrase": TEST_OPERATOR_PASSPHRASE})
    assert r.status_code == 200
    assert "Secure" in r.headers.get("set-cookie", "")


async def test_login_fails_with_wrong_passphrase_and_does_not_leak_it(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "operator_passphrase", TEST_OPERATOR_PASSPHRASE)
    monkeypatch.setattr(settings, "session_signing_key", TEST_SESSION_SIGNING_KEY)

    r = await client.post("/api/operator/session", json={"passphrase": "wrong-passphrase-xyz"})
    assert r.status_code == 401
    assert "wrong-passphrase-xyz" not in r.text
    assert TEST_OPERATOR_PASSPHRASE not in r.text


async def test_login_rate_limits_failed_attempts(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "operator_passphrase", TEST_OPERATOR_PASSPHRASE)
    monkeypatch.setattr(settings, "session_signing_key", TEST_SESSION_SIGNING_KEY)
    # The module-level rate limiter bakes in max_attempts/window at import
    # time (see api/rate_limit.py) — patching `settings` after the fact
    # doesn't reach it, so drive the test off the limiter's own configured
    # value instead of trying to override it.
    max_attempts = operator_router._login_limiter._max_attempts

    for i in range(max_attempts):
        r = await client.post("/api/operator/session", json={"passphrase": f"wrong-{i}"})
        assert r.status_code == 401, f"attempt {i} should be a plain 401, not yet rate-limited"

    r = await client.post("/api/operator/session", json={"passphrase": "wrong-again"})
    assert r.status_code == 429

    # Even the correct passphrase is blocked while rate-limited.
    r = await client.post("/api/operator/session", json={"passphrase": TEST_OPERATOR_PASSPHRASE})
    assert r.status_code == 429


async def test_successful_login_resets_the_failure_counter(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "operator_passphrase", TEST_OPERATOR_PASSPHRASE)
    monkeypatch.setattr(settings, "session_signing_key", TEST_SESSION_SIGNING_KEY)

    await client.post("/api/operator/session", json={"passphrase": "wrong-1"})
    await client.post("/api/operator/session", json={"passphrase": "wrong-2"})
    r = await client.post("/api/operator/session", json={"passphrase": TEST_OPERATOR_PASSPHRASE})
    assert r.status_code == 200

    # Two more wrong attempts right after a successful login shouldn't be
    # blocked — the counter reset on success.
    r = await client.post("/api/operator/session", json={"passphrase": "wrong-3"})
    assert r.status_code == 401
    r = await client.post("/api/operator/session", json={"passphrase": "wrong-4"})
    assert r.status_code == 401


async def test_login_rejects_missing_origin(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "operator_passphrase", TEST_OPERATOR_PASSPHRASE)
    monkeypatch.setattr(settings, "session_signing_key", TEST_SESSION_SIGNING_KEY)

    r = await client.post(
        "/api/operator/session", json={"passphrase": TEST_OPERATOR_PASSPHRASE}, headers={"Origin": ""}
    )
    assert r.status_code == 403


async def test_login_rejects_foreign_origin(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "operator_passphrase", TEST_OPERATOR_PASSPHRASE)
    monkeypatch.setattr(settings, "session_signing_key", TEST_SESSION_SIGNING_KEY)

    r = await client.post(
        "/api/operator/session",
        json={"passphrase": TEST_OPERATOR_PASSPHRASE},
        headers={"Origin": "https://evil.example.com"},
    )
    assert r.status_code == 403


async def test_logout_clears_the_cookie(client, monkeypatch) -> None:
    await login_as_operator(client, monkeypatch)
    r = await client.delete("/api/operator/session")
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert "groundwork_operator_session=" in set_cookie
    # An expired/deleted cookie carries Max-Age=0 (or a past Expires) —
    # either way, not the live session's max_age.
    assert "Max-Age=0" in set_cookie


async def test_mode_live_alone_grants_nothing_without_a_session(client) -> None:
    """The literal Phase 8 acceptance line: a caller sending mode="live"
    without a valid operator session gets no Live capability, on every
    Live-touching route."""
    r = await client.post("/api/plays", json={"objective": "x", "mode": "live"})
    assert r.status_code == 401

    demo_play = await create_play(client)
    r = await client.post(f"/api/plays/{demo_play['id']}/runs", json={"mode": "live"})
    assert r.status_code == 401


async def test_unauthenticated_plays_listing_excludes_live(client, monkeypatch) -> None:
    demo_play = await create_play(client)

    await login_as_operator(client, monkeypatch)
    live_play_r = await client.post("/api/plays", json={"objective": "live one", "mode": "live"})
    assert live_play_r.status_code == 201
    live_play_id = live_play_r.json()["id"]

    r = await client.delete("/api/operator/session")
    assert r.status_code == 200

    listing = await client.get("/api/plays")
    assert listing.status_code == 200
    ids = {p["id"] for p in listing.json()}
    assert demo_play["id"] in ids
    assert live_play_id not in ids


async def test_operator_plays_listing_includes_live(client, monkeypatch) -> None:
    await login_as_operator(client, monkeypatch)
    demo_play = await create_play(client)
    live_play_r = await client.post("/api/plays", json={"objective": "live one", "mode": "live"})
    assert live_play_r.status_code == 201

    listing = await client.get("/api/plays")
    assert listing.status_code == 200
    ids = {p["id"] for p in listing.json()}
    assert demo_play["id"] in ids
    assert live_play_r.json()["id"] in ids


async def test_public_demo_run_detail_remains_readable(client) -> None:
    play = await create_play(client)
    run = await start_run(client, play["id"])
    r = await client.get(f"/api/runs/{run['run_id']}")
    assert r.status_code == 200


async def test_live_run_detail_requires_operator_session(client, session_factory) -> None:
    from groundwork.repositories.plays import PlayRepository
    from groundwork.repositories.runs import RunRepository

    plays = PlayRepository(session_factory)
    runs = RunRepository(session_factory)
    play_id = await plays.create(name="x", objective_text="x", icp_spec={}, mode="live")
    run_id = await runs.create(play_id=play_id, mode="live", seed=1)

    for path in (f"/api/runs/{run_id}", f"/api/runs/{run_id}/prospects", f"/api/runs/{run_id}/evaluation"):
        r = await client.get(path)
        assert r.status_code == 401, f"{path} should require an operator session"


async def test_live_run_detail_readable_with_operator_session(client, session_factory, monkeypatch) -> None:
    await login_as_operator(client, monkeypatch)
    from groundwork.repositories.plays import PlayRepository
    from groundwork.repositories.runs import RunRepository

    plays = PlayRepository(session_factory)
    runs = RunRepository(session_factory)
    play_id = await plays.create(name="x", objective_text="x", icp_spec={}, mode="live")
    run_id = await runs.create(play_id=play_id, mode="live", seed=1)

    for path in (f"/api/runs/{run_id}", f"/api/runs/{run_id}/prospects", f"/api/runs/{run_id}/evaluation"):
        r = await client.get(path)
        assert r.status_code == 200, f"{path} should be readable by an operator"


async def test_live_prospect_requires_operator_session(client, session_factory) -> None:
    from groundwork.repositories.plays import PlayRepository
    from groundwork.repositories.prospects import CompanyRepository, ProspectRepository
    from groundwork.repositories.runs import RunRepository
    from groundwork.models.schemas import CompanySeed

    plays = PlayRepository(session_factory)
    runs = RunRepository(session_factory)
    companies = CompanyRepository(session_factory)
    prospects = ProspectRepository(session_factory)

    play_id = await plays.create(name="x", objective_text="x", icp_spec={}, mode="live")
    run_id = await runs.create(play_id=play_id, mode="live", seed=1)
    company_id = await companies.get_or_create(
        CompanySeed(
            slug="acme", name="Acme", domain="acme.example.com", industry="ai_infrastructure",
            size_band="51-200", employee_count=100,
        ),
        "acme.example.com",
        "acme",
    )
    prospect_id = await prospects.create(
        run_id=run_id, company_id=company_id, dedupe_key="acme", duplicate_of=None, status="RUNNING"
    )

    r = await client.get(f"/api/prospects/{prospect_id}")
    assert r.status_code == 401

    r = await client.post(f"/api/prospects/{prospect_id}/approve", json={"actor": "x"})
    assert r.status_code == 401


async def test_nonexistent_prospect_is_404_not_401(client) -> None:
    r = await client.get("/api/prospects/does-not-exist")
    assert r.status_code == 404
