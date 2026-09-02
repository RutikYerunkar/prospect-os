"""H2 Phase 22 — Mode gating: NEW Live requires BOTH OpenAI and Tavily, no
Live -> fixture-search fallback for either half, and historical Checkpoint G
`provider_profile` rows keep rendering their own `LIVE LLM · FIXTURE SEARCH`
truth unchanged. No network calls anywhere in this file.
"""

from __future__ import annotations

import pytest

from groundwork.api.deps import get_live_runtime, get_live_search_runtime, get_session_factory
from groundwork.main import app
from groundwork.models.enums import Mode
from groundwork.providers.base import ProviderNotConfigured
from groundwork.providers.registry import build_provider_bundle
from tests.api_helpers import login_as_operator


def test_live_unavailable_without_search_runtime_even_with_llm_runtime() -> None:
    with pytest.raises(ProviderNotConfigured):
        build_provider_bundle(Mode.LIVE, seed=1, live_runtime=object(), search_runtime=None)


def test_live_unavailable_without_llm_runtime_even_with_search_runtime() -> None:
    with pytest.raises(ProviderNotConfigured):
        build_provider_bundle(Mode.LIVE, seed=1, live_runtime=None, search_runtime=object())


def test_live_unavailable_with_neither_runtime() -> None:
    with pytest.raises(ProviderNotConfigured):
        build_provider_bundle(Mode.LIVE, seed=1, live_runtime=None, search_runtime=None)


async def test_start_run_422s_without_search_runtime(client, session_factory, monkeypatch) -> None:
    """`_require_search_runtime` in `api/routers/plays.py` — a real
    `LiveProviderRuntime` (OpenAI) but no `LiveSearchRuntime` (Tavily) must
    422, never silently fall back to `DemoSearchProvider`."""
    await login_as_operator(client, monkeypatch)

    class _FakeLiveRuntime:
        pass

    app.dependency_overrides[get_live_runtime] = lambda: _FakeLiveRuntime()
    app.dependency_overrides[get_live_search_runtime] = lambda: None
    try:
        create = await client.post(
            "/api/plays",
            json={"objective": "find robotics companies", "mode": "live", "target_count": 2},
        )
        assert create.status_code == 201
        play_id = create.json()["id"]
        run = await client.post(f"/api/plays/{play_id}/runs", json={"mode": "live"})
        assert run.status_code == 422
        assert "TAVILY_API_KEY" in run.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_live_runtime, None)
        app.dependency_overrides.pop(get_live_search_runtime, None)


async def test_start_run_422s_without_llm_runtime(client, session_factory, monkeypatch) -> None:
    await login_as_operator(client, monkeypatch)

    class _FakeSearchRuntime:
        pass

    app.dependency_overrides[get_live_runtime] = lambda: None
    app.dependency_overrides[get_live_search_runtime] = lambda: _FakeSearchRuntime()
    try:
        create = await client.post(
            "/api/plays",
            json={"objective": "find robotics companies", "mode": "live", "target_count": 2},
        )
        play_id = create.json()["id"]
        run = await client.post(f"/api/plays/{play_id}/runs", json={"mode": "live"})
        assert run.status_code == 422
        assert "OPENAI_API_KEY" in run.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_live_runtime, None)
        app.dependency_overrides.pop(get_live_search_runtime, None)


async def test_settings_endpoint_demo_mode_needs_zero_credentials(client, session_factory) -> None:
    resp = await client.get("/api/settings/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm"]["configured"] is True
    assert body["search"]["configured"] is True
    # No live runtime constructed in the test app (lifespan never runs in
    # the `client` fixture) — real availability correctly reports False,
    # never a fabricated True.
    assert body["live"]["llm_available"] is False
    assert body["live"]["search_available"] is False
    assert body["live"]["available"] is False
    # Checkpoint I1 Phase 8/9
    assert body["live"]["operator_login_configured"] is False  # unset in tests by default
    assert body["live"]["is_operator"] is False
    assert body["max_concurrent_prospects"] == 3


async def test_settings_endpoint_reports_operator_login_configured_and_is_operator(client, monkeypatch) -> None:
    await login_as_operator(client, monkeypatch)
    resp = await client.get("/api/settings/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["live"]["operator_login_configured"] is True
    assert body["live"]["is_operator"] is True


async def test_historical_g_provider_profile_renders_fixture_search_unchanged(client, session_factory, monkeypatch) -> None:
    """A Checkpoint-G-era run persisted `LIVE LLM · FIXTURE SEARCH` in its
    `provider_profile` JSON before H2 ever existed — this function is never
    called again for an existing run, so the row must render back exactly
    as it was persisted, never silently rewritten to the H2 shape.

    Checkpoint I1 Phase 8: reading a live run's detail requires an operator
    session, historical or not.
    """
    await login_as_operator(client, monkeypatch)
    from groundwork.repositories.plays import PlayRepository
    from groundwork.repositories.runs import RunRepository

    plays = PlayRepository(session_factory)
    runs = RunRepository(session_factory)
    play_id = await plays.create(name="historical", objective_text="obj", icp_spec={}, mode="live")
    historical_profile = {
        "mode": "live", "llm_provider": "openai", "model": "gpt-5.6-terra",
        "search_provider": "demo_fixture", "synthetic_search": True, "evidence_origin": "DEMO_FIXTURE",
        "deterministic": False,
    }
    run_id = await runs.create(play_id=play_id, mode="live", seed=1, provider_profile=historical_profile)

    resp = await client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider_profile"]["search_provider"] == "demo_fixture"
    assert body["provider_profile"]["synthetic_search"] is True
