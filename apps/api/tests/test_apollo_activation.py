"""V2-D activation tests: `ENRICHMENT_PROVIDER=none` vs `apollo`, a missing
key 422ing before run start, a stray key never activating Apollo, Demo never
reaching Apollo, and the additive `provider_profile`/`GET /settings/
providers` provenance. No network calls anywhere in this file.
"""

from __future__ import annotations

from groundwork.api.deps import get_enrichment_runtime, get_live_runtime, get_live_search_runtime
from groundwork.config import settings
from groundwork.main import app
from groundwork.models.enums import EnrichmentOrigin, Mode
from groundwork.providers.demo.contact_enrichment import DemoEnrichmentProvider
from groundwork.providers.live.apollo_enrichment import ApolloEnrichmentProvider
from groundwork.providers.profile import build_provider_profile
from groundwork.providers.registry import build_demo_provider_bundle, build_provider_bundle
from tests.api_helpers import create_play, login_as_operator


class _FakeRuntime:
    pass


# --- registry: enrichment is optional in Live Mode, unlike LLM/search -----


def test_live_with_no_enrichment_runtime_leaves_enrichment_none() -> None:
    bundle = build_provider_bundle(
        Mode.LIVE, seed=1, live_runtime=_FakeRuntime(), search_runtime=_FakeRuntime(), enrichment_runtime=None,
    )
    assert bundle.enrichment is None


def test_live_with_enrichment_runtime_wires_apollo_provider() -> None:
    bundle = build_provider_bundle(
        Mode.LIVE, seed=1, live_runtime=_FakeRuntime(), search_runtime=_FakeRuntime(),
        enrichment_runtime=_FakeRuntime(),
    )
    assert isinstance(bundle.enrichment, ApolloEnrichmentProvider)
    assert bundle.enrichment.origin is EnrichmentOrigin.LIVE_PROVIDER
    assert bundle.enrichment.name == "apollo"


def test_demo_bundle_always_uses_demo_enrichment_provider_regardless_of_apollo_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enrichment_provider", "apollo")
    monkeypatch.setattr(settings, "apollo_api_key", "sk-not-real")
    bundle = build_demo_provider_bundle(seed=1)
    assert isinstance(bundle.enrichment, DemoEnrichmentProvider)


# --- API: 422 before run start, stray-key non-activation ------------------


async def test_start_run_422s_when_apollo_selected_without_configured_runtime(client, session_factory, monkeypatch) -> None:
    await login_as_operator(client, monkeypatch)
    monkeypatch.setattr(settings, "enrichment_provider", "apollo")

    app.dependency_overrides[get_live_runtime] = lambda: _FakeRuntime()
    app.dependency_overrides[get_live_search_runtime] = lambda: _FakeRuntime()
    app.dependency_overrides[get_enrichment_runtime] = lambda: None
    try:
        play = await create_play(client)
        run = await client.post(f"/api/plays/{play['id']}/runs", json={"mode": "live"})
        assert run.status_code == 422
        assert "APOLLO_API_KEY" in run.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_live_runtime, None)
        app.dependency_overrides.pop(get_live_search_runtime, None)
        app.dependency_overrides.pop(get_enrichment_runtime, None)


async def test_start_run_never_422s_for_apollo_when_provider_is_none(client, session_factory, monkeypatch) -> None:
    """ENRICHMENT_PROVIDER=none (the default) must never require an Apollo
    runtime — the run still 422s on the (unconfigured-in-tests) LLM runtime,
    but never mentions APOLLO_API_KEY."""
    await login_as_operator(client, monkeypatch)
    monkeypatch.setattr(settings, "enrichment_provider", "none")

    app.dependency_overrides[get_enrichment_runtime] = lambda: None
    try:
        play = await create_play(client)
        run = await client.post(f"/api/plays/{play['id']}/runs", json={"mode": "live"})
        assert run.status_code == 422
        assert "APOLLO_API_KEY" not in run.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_enrichment_runtime, None)


async def test_stray_apollo_key_with_provider_none_never_activates_apollo(client, session_factory, monkeypatch) -> None:
    """A stray `APOLLO_API_KEY` with `ENRICHMENT_PROVIDER=none` must build
    no `ApolloRuntime` and activate no enrichment — modeled here the same
    way `main.py`'s lifespan gate would leave `app.state.enrichment_runtime`
    unset: the dependency returns `None` regardless of the key being
    "configured", because the lifespan guard never even looked at the key
    when the provider isn't `"apollo"`."""
    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setattr(settings, "enrichment_provider", "none")
    monkeypatch.setattr(settings, "apollo_api_key", "sk-stray-not-real")

    resp = await client.get("/api/settings/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enrichment"]["name"] == "none"
    assert body["live"]["enrichment_provider"] == "none"
    assert body["live"]["enrichment_available"] is False


# --- GET /settings/providers ------------------------------------------


async def test_settings_endpoint_reports_apollo_configured_state(client, session_factory, monkeypatch) -> None:
    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setattr(settings, "enrichment_provider", "apollo")
    monkeypatch.setattr(settings, "apollo_api_key", None)

    resp = await client.get("/api/settings/providers")
    body = resp.json()
    assert body["enrichment"]["name"] == "apollo"
    assert body["enrichment"]["configured"] is False  # key present -> configured, but unset here
    assert body["live"]["enrichment_provider"] == "apollo"
    assert body["live"]["enrichment_available"] is False  # no runtime constructed in tests

    monkeypatch.setattr(settings, "apollo_api_key", "sk-not-real")
    resp2 = await client.get("/api/settings/providers")
    assert resp2.json()["enrichment"]["configured"] is True


async def test_settings_endpoint_never_exposes_the_apollo_key(client, session_factory, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enrichment_provider", "apollo")
    monkeypatch.setattr(settings, "apollo_api_key", "sk-super-secret-value")
    resp = await client.get("/api/settings/providers")
    assert "sk-super-secret-value" not in resp.text


async def test_demo_mode_settings_endpoint_reports_demo_fixture_enrichment(client, session_factory) -> None:
    resp = await client.get("/api/settings/providers")
    body = resp.json()
    assert body["enrichment"]["name"] == "demo_fixture"
    assert body["enrichment"]["configured"] is True


# --- provider_profile provenance (§ "PROVIDER PROFILE / SETTINGS") --------


def test_provider_profile_demo_mode() -> None:
    profile = build_provider_profile(Mode.DEMO, settings)
    assert profile["enrichment_provider"] == "demo_fixture"
    assert profile["enrichment_origin"] == "DEMO_FIXTURE"


def test_provider_profile_live_mode_enrichment_none(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enrichment_provider", "none")
    profile = build_provider_profile(Mode.LIVE, settings)
    assert profile["enrichment_provider"] is None
    assert profile["enrichment_origin"] is None


def test_provider_profile_live_mode_enrichment_apollo(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enrichment_provider", "apollo")
    profile = build_provider_profile(Mode.LIVE, settings)
    assert profile["enrichment_provider"] == "apollo"
    assert profile["enrichment_origin"] == "LIVE_PROVIDER"


# --- redaction --------------------------------------------------------


def test_apollo_key_is_added_to_redaction_choke_point(monkeypatch) -> None:
    from groundwork.observability.redact import redact

    monkeypatch.setattr(settings, "apollo_api_key", "sk-apollo-canary-secret-value")
    text = "AuthenticationError: invalid key sk-apollo-canary-secret-value rejected by upstream"
    out = redact(text)
    assert "sk-apollo-canary-secret-value" not in out
    assert "[REDACTED]" in out
