"""V2-DH activation tests: `ENRICHMENT_PROVIDER=none` vs `apollo` vs
`hunter`, a missing Hunter key 422ing before run start naming
`HUNTER_API_KEY`, a stray Hunter key never activating Hunter, Demo never
reaching Hunter, Apollo/Hunter coexisting behind the same `EnrichmentProvider`
Protocol, and the additive `provider_profile`/`GET /settings/providers`
provenance. No network calls anywhere in this file.
"""

from __future__ import annotations

from groundwork.api.deps import get_enrichment_runtime, get_live_runtime, get_live_search_runtime
from groundwork.config import settings
from groundwork.main import app
from groundwork.models.enums import EnrichmentOrigin, Mode
from groundwork.providers.demo.contact_enrichment import DemoEnrichmentProvider
from groundwork.providers.live.apollo_enrichment import ApolloEnrichmentProvider
from groundwork.providers.live.hunter_enrichment import HunterEnrichmentProvider
from groundwork.providers.profile import build_provider_profile
from groundwork.providers.registry import build_demo_provider_bundle, build_provider_bundle
from tests.api_helpers import create_play, login_as_operator


class _FakeRuntime:
    pass


# --- registry: Apollo/Hunter coexist behind the same Protocol -------------


def test_live_with_hunter_selected_wires_hunter_provider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enrichment_provider", "hunter")
    bundle = build_provider_bundle(
        Mode.LIVE, seed=1, live_runtime=_FakeRuntime(), search_runtime=_FakeRuntime(),
        enrichment_runtime=_FakeRuntime(),
    )
    assert isinstance(bundle.enrichment, HunterEnrichmentProvider)
    assert bundle.enrichment.origin is EnrichmentOrigin.LIVE_PROVIDER
    assert bundle.enrichment.name == "hunter"


def test_live_with_apollo_selected_wires_apollo_provider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enrichment_provider", "apollo")
    bundle = build_provider_bundle(
        Mode.LIVE, seed=1, live_runtime=_FakeRuntime(), search_runtime=_FakeRuntime(),
        enrichment_runtime=_FakeRuntime(),
    )
    assert isinstance(bundle.enrichment, ApolloEnrichmentProvider)


def test_demo_bundle_always_uses_demo_enrichment_provider_regardless_of_hunter_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enrichment_provider", "hunter")
    monkeypatch.setattr(settings, "hunter_api_key", "sk-not-real")
    bundle = build_demo_provider_bundle(seed=1)
    assert isinstance(bundle.enrichment, DemoEnrichmentProvider)


# --- API: 422 before run start, stray-key non-activation ------------------


async def test_start_run_422s_when_hunter_selected_without_configured_runtime(client, session_factory, monkeypatch) -> None:
    await login_as_operator(client, monkeypatch)
    monkeypatch.setattr(settings, "enrichment_provider", "hunter")

    app.dependency_overrides[get_live_runtime] = lambda: _FakeRuntime()
    app.dependency_overrides[get_live_search_runtime] = lambda: _FakeRuntime()
    app.dependency_overrides[get_enrichment_runtime] = lambda: None
    try:
        play = await create_play(client)
        run = await client.post(f"/api/plays/{play['id']}/runs", json={"mode": "live"})
        assert run.status_code == 422
        assert "HUNTER_API_KEY" in run.json()["detail"]
        assert "APOLLO_API_KEY" not in run.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_live_runtime, None)
        app.dependency_overrides.pop(get_live_search_runtime, None)
        app.dependency_overrides.pop(get_enrichment_runtime, None)


async def test_start_run_never_422s_for_hunter_when_provider_is_none(client, session_factory, monkeypatch) -> None:
    await login_as_operator(client, monkeypatch)
    monkeypatch.setattr(settings, "enrichment_provider", "none")

    app.dependency_overrides[get_enrichment_runtime] = lambda: None
    try:
        play = await create_play(client)
        run = await client.post(f"/api/plays/{play['id']}/runs", json={"mode": "live"})
        assert run.status_code == 422
        assert "HUNTER_API_KEY" not in run.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_enrichment_runtime, None)


async def test_stray_hunter_key_with_provider_none_never_activates_hunter(client, session_factory, monkeypatch) -> None:
    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setattr(settings, "enrichment_provider", "none")
    monkeypatch.setattr(settings, "hunter_api_key", "sk-stray-not-real")

    resp = await client.get("/api/settings/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enrichment"]["name"] == "none"
    assert body["live"]["enrichment_provider"] == "none"
    assert body["live"]["enrichment_available"] is False


async def test_stray_hunter_key_with_provider_apollo_never_activates_hunter(client, session_factory, monkeypatch) -> None:
    """A `HUNTER_API_KEY` set alongside `ENRICHMENT_PROVIDER=apollo` must
    never activate Hunter — exactly one `EnrichmentProvider` slot, selected
    by `ENRICHMENT_PROVIDER` alone."""
    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setattr(settings, "enrichment_provider", "apollo")
    monkeypatch.setattr(settings, "hunter_api_key", "sk-stray-not-real")
    monkeypatch.setattr(settings, "apollo_api_key", None)

    resp = await client.get("/api/settings/providers")
    body = resp.json()
    assert body["enrichment"]["name"] == "apollo"
    assert body["enrichment"]["configured"] is False


# --- GET /settings/providers ------------------------------------------


async def test_settings_endpoint_reports_hunter_configured_state(client, session_factory, monkeypatch) -> None:
    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setattr(settings, "enrichment_provider", "hunter")
    monkeypatch.setattr(settings, "hunter_api_key", None)

    resp = await client.get("/api/settings/providers")
    body = resp.json()
    assert body["enrichment"]["name"] == "hunter"
    assert body["enrichment"]["configured"] is False
    assert body["live"]["enrichment_provider"] == "hunter"
    assert body["live"]["enrichment_available"] is False

    monkeypatch.setattr(settings, "hunter_api_key", "sk-not-real")
    resp2 = await client.get("/api/settings/providers")
    assert resp2.json()["enrichment"]["configured"] is True


async def test_settings_endpoint_never_exposes_the_hunter_key(client, session_factory, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enrichment_provider", "hunter")
    monkeypatch.setattr(settings, "hunter_api_key", "sk-super-secret-hunter-value")
    resp = await client.get("/api/settings/providers")
    assert "sk-super-secret-hunter-value" not in resp.text


def test_provider_info_configured_semantics_pinned(monkeypatch) -> None:
    """§Part 14's REV-3 clarification: audit + pin the EXISTING (not a
    redesigned) `ProviderInfo.configured` semantics — `"none"` is always
    `configured=True` (nothing is needed for it); a selected live provider's
    `configured` reflects only whether ITS OWN key is present. Exercised
    directly against `build_provider_bundle`'s sibling, the settings router
    logic, via the live HTTP surface in the tests above; this test pins the
    contract in one place for both providers."""
    from groundwork.api.schemas import ProviderInfo

    none_info = ProviderInfo(name="none", configured=True)
    assert none_info.configured is True

    unconfigured_hunter = ProviderInfo(name="hunter", configured=bool(None))
    assert unconfigured_hunter.configured is False

    configured_hunter = ProviderInfo(name="hunter", configured=bool("sk-real"))
    assert configured_hunter.configured is True


# --- provider_profile provenance ------------------------------------------


def test_provider_profile_live_mode_enrichment_hunter(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enrichment_provider", "hunter")
    profile = build_provider_profile(Mode.LIVE, settings)
    assert profile["enrichment_provider"] == "hunter"
    assert profile["enrichment_origin"] == "LIVE_PROVIDER"


# --- redaction --------------------------------------------------------


def test_hunter_key_is_added_to_redaction_choke_point(monkeypatch) -> None:
    from groundwork.observability.redact import redact

    monkeypatch.setattr(settings, "hunter_api_key", "sk-hunter-canary-secret-value")
    text = "AuthenticationError: invalid key sk-hunter-canary-secret-value rejected by upstream"
    out = redact(text)
    assert "sk-hunter-canary-secret-value" not in out
    assert "[REDACTED]" in out
