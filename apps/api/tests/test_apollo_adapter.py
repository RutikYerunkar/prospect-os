"""V2-D adapter tests for `ApolloEnrichmentProvider`. Every HTTP exchange is
scripted via `httpx.MockTransport` (`tests/live_enrichment_helpers.py`) — no
automated test may hit the real Apollo API.
"""

from __future__ import annotations

import json

import httpx
import pytest

from groundwork.models.enums import EmailVerificationState, EnrichmentAttemptStatus, EnrichmentOrigin
from groundwork.providers.contact_base import (
    EnrichmentAuthError,
    EnrichmentInvalidResponse,
    EnrichmentProviderUnavailable,
    EnrichmentRateLimited,
    EnrichmentTimeout,
    PersonEnrichmentQuery,
)
from groundwork.providers.live.enrichment_runtime import APOLLO_API_ORIGIN, APOLLO_PEOPLE_MATCH_PATH
from tests.live_enrichment_helpers import apollo_person, make_enrichment_provider, match_response


def _query(**overrides) -> PersonEnrichmentQuery:
    kwargs = dict(
        full_name="Priya Natarajan", title="VP of Sales",
        company_name="Acme Robotics", company_domain="acme.example.com",
    )
    kwargs.update(overrides)
    return PersonEnrichmentQuery(**kwargs)


# --- mapping -----------------------------------------------------------


async def test_successful_match_maps_all_documented_fields() -> None:
    provider, transport = make_enrichment_provider([(200, match_response())])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")

    assert result.matched is True
    assert result.provider_person_id == "person-abc123"
    assert result.origin is EnrichmentOrigin.LIVE_PROVIDER
    assert result.email.address == "priya.natarajan@acme.example.com"
    assert result.email.provider_status == "verified"
    assert result.linkedin.profile_url == "https://www.linkedin.com/in/priya-natarajan"
    assert result.linkedin.asserted_full_name == "Priya Natarajan"
    assert result.linkedin.asserted_company_name == "Acme Robotics"
    assert result.linkedin.asserted_company_domain == "acme.example.com"
    assert result.linkedin.asserted_title == "VP of Sales"
    assert result.raw_digest and len(result.raw_digest) == 16
    assert result.telemetry and result.telemetry[0].status == EnrichmentAttemptStatus.OK


async def test_email_status_verified_maps_to_verified() -> None:
    provider, _ = make_enrichment_provider([(200, match_response())])
    assert provider.email_status_map["verified"] == EmailVerificationState.VERIFIED


async def test_email_status_extrapolated_maps_to_risky() -> None:
    provider, _ = make_enrichment_provider([(200, match_response())])
    assert provider.email_status_map["extrapolated"] == EmailVerificationState.RISKY


async def test_confidence_and_catch_all_remain_none() -> None:
    provider, _ = make_enrichment_provider([(200, match_response())])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.provider_confidence is None
    assert result.email.is_catch_all is None


async def test_asserted_company_domain_only_from_response_never_query_backfill() -> None:
    """§Part 4: `asserted_company_domain` must never be back-filled from the
    query's `company_domain` — only from Apollo's own
    `organization.primary_domain`, and only when actually supplied."""
    person = apollo_person(organization={"id": "org-1", "name": "Acme Robotics"})  # no primary_domain
    provider, _ = make_enrichment_provider([(200, match_response(person=person))])
    result = await provider.enrich_person(
        _query(company_domain="acme.example.com"), ctx_key="r1:p1:contact_enrichment"
    )
    assert result.linkedin.asserted_company_domain is None


async def test_full_name_fallback_to_first_last_when_name_absent() -> None:
    person = apollo_person(name=None, first_name="Priya", last_name="Natarajan")
    provider, _ = make_enrichment_provider([(200, match_response(person=person))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.linkedin.asserted_full_name == "Priya Natarajan"


async def test_full_name_fallback_absent_when_both_missing() -> None:
    person = apollo_person(name=None, first_name=None, last_name=None)
    provider, _ = make_enrichment_provider([(200, match_response(person=person))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.linkedin.asserted_full_name is None


async def test_linkedin_url_absent_still_returns_a_matched_result() -> None:
    person = apollo_person(linkedin_url=None)
    provider, _ = make_enrichment_provider([(200, match_response(person=person))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.matched is True
    assert result.linkedin.profile_url is None


# --- outbound HTTP contract ---------------------------------------------


async def test_outbound_contract_query_params_no_json_body_all_opt_outs_false() -> None:
    provider, transport = make_enrichment_provider([(200, match_response())])
    await provider.enrich_person(_query(full_name="Priya Natarajan"), ctx_key="r1:p1:contact_enrichment")

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.url.path == APOLLO_PEOPLE_MATCH_PATH
    assert str(request.url).startswith(APOLLO_API_ORIGIN)
    assert request.content in (b"", None)
    assert request.headers.get("x-api-key") == "test-apollo-not-real"

    params = dict(httpx.QueryParams(request.url.query.decode()))
    assert params["name"] == "Priya Natarajan"
    assert params["domain"] == "acme.example.com"
    assert params["reveal_personal_emails"] == "false"
    assert params["reveal_phone_number"] == "false"
    assert params["run_waterfall_email"] == "false"
    assert params["run_waterfall_phone"] == "false"
    assert "webhook_url" not in params


async def test_full_name_sent_whole_never_split() -> None:
    provider, transport = make_enrichment_provider([(200, match_response())])
    await provider.enrich_person(
        _query(full_name="Priya K. Natarajan"), ctx_key="r1:p1:contact_enrichment"
    )
    params = dict(httpx.QueryParams(transport.requests[0].url.query.decode()))
    assert params["name"] == "Priya K. Natarajan"


async def test_endpoint_and_origin_are_pinned() -> None:
    assert APOLLO_API_ORIGIN == "https://api.apollo.io"
    assert APOLLO_PEOPLE_MATCH_PATH == "/api/v1/people/match"


# --- response envelope / invalid-response handling -----------------------


async def test_missing_person_key_is_invalid_response() -> None:
    provider, transport = make_enrichment_provider([(200, {"not_person": {}})])
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1  # never retried — permanent


async def test_person_missing_id_is_invalid_response() -> None:
    provider, transport = make_enrichment_provider([(200, {"person": {"name": "No Id"}})])
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")


async def test_person_null_id_is_invalid_response() -> None:
    provider, transport = make_enrichment_provider([(200, {"person": {"id": None}})])
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")


async def test_non_dict_response_body_is_invalid_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps([1, 2, 3]), request=request)

    provider, _ = make_enrichment_provider(handler=handler)
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")


async def test_non_json_response_body_is_invalid_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json", request=request)

    provider, _ = make_enrichment_provider(handler=handler)
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")


async def test_no_match_shape_is_never_invented_as_matched_false() -> None:
    """The exact HTTP-200 no-match representation is unverified (§Part 15
    risk 2) — a plausible-looking `{"person": null}` (or an empty dict) must
    fail closed to `EnrichmentInvalidResponse`, never be silently treated as
    a legitimate `matched=False` observation."""
    provider, _ = make_enrichment_provider([(200, {"person": None})])
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")


# --- error / retry policy -------------------------------------------------


async def test_timeout_retries_then_raises_enrichment_timeout() -> None:
    provider, transport = make_enrichment_provider(
        [httpx.ConnectTimeout("boom"), httpx.ConnectTimeout("boom again")],
        settings_overrides={"apollo_max_transport_retries": 1},
    )
    with pytest.raises(EnrichmentTimeout) as excinfo:
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert len(excinfo.value.telemetry) == 2  # 1 + max_transport_retries(1)
    assert all(t.status == EnrichmentAttemptStatus.TIMEOUT for t in excinfo.value.telemetry)
    assert transport.calls == 2


async def test_transport_error_retries_then_raises_provider_unavailable() -> None:
    provider, transport = make_enrichment_provider(
        [httpx.ConnectError("refused"), httpx.ConnectError("refused again")],
        settings_overrides={"apollo_max_transport_retries": 1},
    )
    with pytest.raises(EnrichmentProviderUnavailable):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 2


async def test_401_is_permanent_auth_error_no_retry() -> None:
    provider, transport = make_enrichment_provider(
        [(401, {"error": "invalid key"})], settings_overrides={"apollo_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentAuthError) as excinfo:
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1
    assert excinfo.value.telemetry[0].status == EnrichmentAttemptStatus.AUTH_ERROR


async def test_403_is_permanent_auth_error_no_retry() -> None:
    provider, transport = make_enrichment_provider(
        [(403, {"error": "forbidden"})], settings_overrides={"apollo_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentAuthError):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_404_is_permanent_invalid_response() -> None:
    provider, transport = make_enrichment_provider(
        [(404, {"error": "not found"})], settings_overrides={"apollo_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_422_is_permanent_invalid_response() -> None:
    provider, transport = make_enrichment_provider(
        [(422, {"error": "unprocessable"})], settings_overrides={"apollo_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_unknown_4xx_is_permanent_invalid_response() -> None:
    provider, transport = make_enrichment_provider(
        [(418, {"error": "teapot"})], settings_overrides={"apollo_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_unexpected_3xx_is_permanent_invalid_response() -> None:
    provider, transport = make_enrichment_provider(
        [(301, {})], settings_overrides={"apollo_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_429_is_retryable_rate_limited() -> None:
    provider, transport = make_enrichment_provider(
        [(429, {"error": "rate limited"}), (429, {"error": "rate limited"})],
        settings_overrides={"apollo_max_transport_retries": 1},
    )
    with pytest.raises(EnrichmentRateLimited) as excinfo:
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 2
    assert all(t.status == EnrichmentAttemptStatus.RATE_LIMITED for t in excinfo.value.telemetry)


async def test_5xx_is_retryable_then_raises_provider_unavailable() -> None:
    provider, transport = make_enrichment_provider(
        [(500, {}), (503, {})], settings_overrides={"apollo_max_transport_retries": 1}
    )
    with pytest.raises(EnrichmentProviderUnavailable):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 2


async def test_transport_retry_then_succeeds() -> None:
    provider, transport = make_enrichment_provider(
        [httpx.ConnectTimeout("boom"), (200, match_response())],
        settings_overrides={"apollo_max_transport_retries": 1},
    )
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.matched is True
    assert transport.calls == 2
    assert len(result.telemetry) == 2


async def test_no_invented_quota_mapping_for_402() -> None:
    """§Part 4: 402 is never assumed to mean quota exhausted — it is just
    another unknown 4xx, permanent, no `EnrichmentQuotaExceeded` invented."""
    provider, transport = make_enrichment_provider(
        [(402, {"error": "payment required"})], settings_overrides={"apollo_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


# --- budget ---------------------------------------------------------------


async def test_budget_denial_makes_zero_network_calls() -> None:
    class _AlwaysDenyBudget:
        async def reserve_call(self) -> bool:
            return False

    provider, transport = make_enrichment_provider([], budget=_AlwaysDenyBudget())
    from groundwork.providers.contact_base import EnrichmentBudgetExceeded

    with pytest.raises(EnrichmentBudgetExceeded) as excinfo:
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 0
    assert excinfo.value.telemetry[0].status == EnrichmentAttemptStatus.NOT_ATTEMPTED_BUDGET


async def test_budget_reserved_once_per_logical_call_not_per_transport_attempt() -> None:
    calls_reserved = 0

    class _CountingBudget:
        async def reserve_call(self) -> bool:
            nonlocal calls_reserved
            calls_reserved += 1
            return True

    provider, transport = make_enrichment_provider(
        [httpx.ConnectTimeout("boom"), (200, match_response())],
        settings_overrides={"apollo_max_transport_retries": 1},
        budget=_CountingBudget(),
    )
    await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert calls_reserved == 1
    assert transport.calls == 2


# --- telemetry --------------------------------------------------------


async def test_unique_attempt_numbering_and_shared_call_group_id() -> None:
    provider, transport = make_enrichment_provider(
        [httpx.ConnectTimeout("boom"), (200, match_response())],
        settings_overrides={"apollo_max_transport_retries": 1},
    )
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert [t.attempt for t in result.telemetry] == [1, 2]
    assert result.telemetry[0].call_group_id == result.telemetry[1].call_group_id
    from groundwork.providers.contact_base import EnrichmentAttemptKind

    assert result.telemetry[0].attempt_kind == EnrichmentAttemptKind.INITIAL
    assert result.telemetry[1].attempt_kind == EnrichmentAttemptKind.TRANSPORT_RETRY


async def test_cost_and_credits_stay_none() -> None:
    provider, _ = make_enrichment_provider(
        [(200, match_response())], settings_overrides={"apollo_price_usd_per_credit": 0.01}
    )
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    for t in result.telemetry:
        assert t.credits_used is None
        assert t.cost_usd is None


async def test_no_raw_payload_persisted_only_digest() -> None:
    provider, _ = make_enrichment_provider([(200, match_response())])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert isinstance(result.raw_digest, str)
    assert "priya.natarajan@acme.example.com" not in result.raw_digest


# --- provider purity ----------------------------------------------------


async def test_provider_purity_no_repository_or_sqlalchemy_imports() -> None:
    import ast
    import inspect

    from groundwork.providers.live import apollo_enrichment, enrichment_runtime

    for module in (apollo_enrichment, enrichment_runtime):
        tree = ast.parse(inspect.getsource(module))
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
        for name in imported_modules:
            assert not name.startswith("sqlalchemy"), f"{module.__name__} imports {name}"
            assert not name.startswith("groundwork.repositories"), f"{module.__name__} imports {name}"
            assert name != "groundwork.models.tables", f"{module.__name__} imports {name}"


async def test_no_arbitrary_http_fetch_path() -> None:
    import inspect

    from groundwork.providers.live import apollo_enrichment

    source = inspect.getsource(apollo_enrichment)
    assert "requests.get(" not in source
    assert "requests.post(" not in source
