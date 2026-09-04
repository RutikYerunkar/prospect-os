"""V2-DH adapter tests for `HunterEnrichmentProvider`. Every HTTP exchange is
scripted via `httpx.MockTransport` (`tests/live_hunter_helpers.py`) — no
automated test may hit the real Hunter API.
"""

from __future__ import annotations

import json

import httpx
import pytest

from groundwork.models.enums import EmailVerificationState, EnrichmentAttemptStatus, EnrichmentOrigin
from groundwork.providers.contact_base import (
    EnrichmentAuthError,
    EnrichmentBudgetExceeded,
    EnrichmentInvalidResponse,
    EnrichmentProviderUnavailable,
    EnrichmentQuotaExceeded,
    EnrichmentRateLimited,
    EnrichmentTimeout,
    PersonEnrichmentQuery,
)
from groundwork.providers.live.hunter_runtime import HUNTER_API_ORIGIN, HUNTER_EMAIL_FINDER_PATH
from tests.live_hunter_helpers import email_finder_response, hunter_data, make_hunter_provider


def _query(**overrides) -> PersonEnrichmentQuery:
    kwargs = dict(
        full_name="Priya Natarajan", title="VP of Sales",
        company_name="Acme Robotics", company_domain="acme.example.com",
    )
    kwargs.update(overrides)
    return PersonEnrichmentQuery(**kwargs)


# --- mapping -----------------------------------------------------------


async def test_successful_match_maps_all_documented_fields() -> None:
    provider, transport = make_hunter_provider([(200, email_finder_response())])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")

    assert result.matched is True
    assert result.provider_person_id is None
    assert result.origin is EnrichmentOrigin.LIVE_PROVIDER
    assert result.email.address == "priya.natarajan@acme.example.com"
    assert result.email.provider_status == "valid"
    assert result.email.provider_confidence == pytest.approx(0.92)
    assert result.email.is_catch_all is False
    assert result.linkedin.profile_url == "https://www.linkedin.com/in/priya-natarajan"
    assert result.linkedin.asserted_full_name == "Priya Natarajan"
    assert result.linkedin.asserted_company_name == "Acme Robotics"
    assert result.linkedin.asserted_company_domain is None
    assert result.linkedin.asserted_title == "VP of Sales"
    assert result.raw_digest and len(result.raw_digest) == 16
    assert result.telemetry and result.telemetry[0].status == EnrichmentAttemptStatus.OK


async def test_email_status_valid_maps_to_verified() -> None:
    provider, _ = make_hunter_provider([(200, email_finder_response())])
    assert provider.email_status_map["valid"] == EmailVerificationState.VERIFIED


async def test_email_status_accept_all_maps_to_risky() -> None:
    provider, _ = make_hunter_provider([(200, email_finder_response())])
    assert provider.email_status_map["accept_all"] == EmailVerificationState.RISKY


async def test_email_status_unknown_maps_to_unverified() -> None:
    provider, _ = make_hunter_provider([(200, email_finder_response())])
    assert provider.email_status_map["unknown"] == EmailVerificationState.UNVERIFIED


async def test_email_status_future_value_falls_closed_to_unverified() -> None:
    data = hunter_data(verification_status="some_future_status")
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.provider_status == "some_future_status"
    assert provider.email_status_map.get(result.email.provider_status, EmailVerificationState.UNVERIFIED) == (
        EmailVerificationState.UNVERIFIED
    )


async def test_verification_absent_leaves_provider_status_none() -> None:
    data = hunter_data(include_verification=False)
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.provider_status is None


async def test_score_100_maps_to_confidence_1_but_never_promotes_verification() -> None:
    data = hunter_data(score=100, verification_status="unknown")
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.provider_confidence == pytest.approx(1.0)
    assert provider.email_status_map[result.email.provider_status] == EmailVerificationState.UNVERIFIED


async def test_score_0_maps_to_confidence_0() -> None:
    data = hunter_data(score=0)
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.provider_confidence == 0.0


async def test_score_50_maps_to_confidence_half() -> None:
    data = hunter_data(score=50)
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.provider_confidence == pytest.approx(0.5)


async def test_score_out_of_range_fails_closed_to_none() -> None:
    data = hunter_data(score=150)
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.provider_confidence is None


async def test_score_negative_fails_closed_to_none() -> None:
    data = hunter_data(score=-5)
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.provider_confidence is None


async def test_score_bool_fails_closed_to_none() -> None:
    """`isinstance(True, int)` is `True` in Python — a bool score must not
    be silently accepted as a numeric one."""
    data = hunter_data(score=True)
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.provider_confidence is None


async def test_score_non_numeric_fails_closed_to_none() -> None:
    data = hunter_data(score="high")
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.provider_confidence is None


async def test_accept_all_true_is_preserved() -> None:
    data = hunter_data(accept_all=True)
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.is_catch_all is True


async def test_accept_all_non_bool_fails_closed_to_none() -> None:
    data = hunter_data(accept_all=None)
    data["accept_all"] = "yes"  # a malformed non-bool value
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.email.is_catch_all is None


async def test_linkedin_url_absent_still_returns_a_matched_result() -> None:
    data = hunter_data(linkedin_url=None)
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.matched is True
    assert result.linkedin.profile_url is None


async def test_asserted_company_domain_always_none_even_when_data_domain_present() -> None:
    """§Part 5's pinned rule: `data.domain` (which echoes back the query's
    own `domain` param) must NEVER self-confirm company identity."""
    data = hunter_data(domain="acme.example.com")
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(company_domain="acme.example.com"), ctx_key="r1:p1:contact_enrichment")
    assert result.linkedin.asserted_company_domain is None


async def test_full_name_combines_first_and_last_from_response_only() -> None:
    data = hunter_data(first_name="Priya", last_name="Natarajan")
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(full_name="P. N."), ctx_key="r1:p1:contact_enrichment")
    assert result.linkedin.asserted_full_name == "Priya Natarajan"


async def test_full_name_absent_when_both_response_names_missing() -> None:
    data = hunter_data(first_name=None, last_name=None)
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.linkedin.asserted_full_name is None


async def test_provider_person_id_always_none() -> None:
    provider, _ = make_hunter_provider([(200, email_finder_response())])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.provider_person_id is None


async def test_no_raw_payload_persisted_only_digest() -> None:
    provider, _ = make_hunter_provider([(200, email_finder_response())])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert isinstance(result.raw_digest, str)
    assert "priya.natarajan@acme.example.com" not in result.raw_digest


async def test_legitimate_empty_email_is_not_matched_never_invalid_response() -> None:
    data = hunter_data(email=None)
    provider, transport = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.matched is False
    assert result.email.address is None
    assert transport.calls == 1


async def test_legitimate_empty_string_email_treated_as_no_match() -> None:
    data = hunter_data(email="")
    provider, _ = make_hunter_provider([(200, email_finder_response(data=data))])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.matched is False
    assert result.email.address is None


async def test_missing_data_object_treated_as_legitimate_no_match() -> None:
    provider, transport = make_hunter_provider([(200, {"meta": {}})])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.matched is False
    assert result.email is not None
    assert result.email.address is None
    assert transport.calls == 1


async def test_malformed_non_string_email_is_invalid_response() -> None:
    data = hunter_data()
    data["email"] = 12345
    provider, transport = make_hunter_provider([(200, email_finder_response(data=data))])
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1  # never retried — permanent


async def test_malformed_data_field_not_an_object_is_invalid_response() -> None:
    provider, transport = make_hunter_provider([(200, {"data": "not-an-object"})])
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


# --- name handling ---------------------------------------------------------


async def test_whitespace_only_full_name_makes_zero_network_calls_and_is_not_found() -> None:
    provider, transport = make_hunter_provider([])
    result = await provider.enrich_person(_query(full_name="   "), ctx_key="r1:p1:contact_enrichment")
    assert result.matched is False
    assert transport.calls == 0
    assert result.telemetry[0].status == EnrichmentAttemptStatus.NOT_FOUND


async def test_none_full_name_makes_zero_network_calls() -> None:
    provider, transport = make_hunter_provider([])
    result = await provider.enrich_person(_query(full_name=None), ctx_key="r1:p1:contact_enrichment")
    assert result.matched is False
    assert transport.calls == 0


async def test_multi_token_name_and_honorifics_transmitted_unchanged() -> None:
    provider, transport = make_hunter_provider([(200, email_finder_response())])
    await provider.enrich_person(_query(full_name="Dr. Priya K. Natarajan Jr."), ctx_key="r1:p1:contact_enrichment")
    params = dict(httpx.QueryParams(transport.requests[0].url.query.decode()))
    assert params["full_name"] == "Dr. Priya K. Natarajan Jr."


# --- outbound HTTP contract ---------------------------------------------


async def test_outbound_contract_query_exactly_domain_and_full_name_get_no_body() -> None:
    provider, transport = make_hunter_provider([(200, email_finder_response())])
    await provider.enrich_person(_query(full_name="Priya Natarajan"), ctx_key="r1:p1:contact_enrichment")

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url.path == HUNTER_EMAIL_FINDER_PATH
    assert str(request.url).startswith(HUNTER_API_ORIGIN)
    assert request.content in (b"", None)
    assert request.headers.get("x-api-key") == "test-hunter-not-real"

    params = dict(httpx.QueryParams(request.url.query.decode()))
    assert params == {"domain": "acme.example.com", "full_name": "Priya Natarajan"}
    assert "api_key" not in params
    assert "first_name" not in params
    assert "last_name" not in params
    assert "company" not in params
    assert "linkedin_handle" not in params
    assert "max_duration" not in params
    assert "title" not in params


async def test_api_key_absent_from_request_url_string() -> None:
    provider, transport = make_hunter_provider([(200, email_finder_response())])
    await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert "test-hunter-not-real" not in str(transport.requests[0].url)


async def test_endpoint_and_origin_are_pinned() -> None:
    assert HUNTER_API_ORIGIN == "https://api.hunter.io"
    assert HUNTER_EMAIL_FINDER_PATH == "/v2/email-finder"


# --- error / retry policy -------------------------------------------------


async def test_timeout_retries_then_raises_enrichment_timeout() -> None:
    provider, transport = make_hunter_provider(
        [httpx.ConnectTimeout("boom"), httpx.ConnectTimeout("boom again")],
        settings_overrides={"hunter_max_transport_retries": 1},
    )
    with pytest.raises(EnrichmentTimeout) as excinfo:
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert len(excinfo.value.telemetry) == 2
    assert all(t.status == EnrichmentAttemptStatus.TIMEOUT for t in excinfo.value.telemetry)
    assert transport.calls == 2


async def test_transport_error_retries_then_raises_provider_unavailable() -> None:
    provider, transport = make_hunter_provider(
        [httpx.ConnectError("refused"), httpx.ConnectError("refused again")],
        settings_overrides={"hunter_max_transport_retries": 1},
    )
    with pytest.raises(EnrichmentProviderUnavailable):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 2


async def test_401_is_permanent_auth_error_no_retry() -> None:
    provider, transport = make_hunter_provider(
        [(401, {"errors": [{"id": "unauthorized", "code": 401, "details": "invalid key"}]})],
        settings_overrides={"hunter_max_transport_retries": 2},
    )
    with pytest.raises(EnrichmentAuthError) as excinfo:
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1
    assert excinfo.value.telemetry[0].status == EnrichmentAttemptStatus.AUTH_ERROR


async def test_403_is_retryable_rate_limited() -> None:
    provider, transport = make_hunter_provider(
        [(403, {"errors": [{"id": "forbidden"}]}), (403, {"errors": [{"id": "forbidden"}]})],
        settings_overrides={"hunter_max_transport_retries": 1},
    )
    with pytest.raises(EnrichmentRateLimited) as excinfo:
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 2
    assert all(t.status == EnrichmentAttemptStatus.RATE_LIMITED for t in excinfo.value.telemetry)


async def test_404_is_permanent_invalid_response() -> None:
    provider, transport = make_hunter_provider(
        [(404, {"errors": [{"id": "not_found"}]})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_422_is_permanent_invalid_response() -> None:
    provider, transport = make_hunter_provider(
        [(422, {"errors": [{"id": "invalid_argument"}]})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_429_is_permanent_quota_exhausted_never_retried() -> None:
    provider, transport = make_hunter_provider(
        [(429, {"errors": [{"id": "too_many_requests"}]})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentQuotaExceeded) as excinfo:
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1
    assert excinfo.value.telemetry[0].status == EnrichmentAttemptStatus.QUOTA_EXHAUSTED


async def test_451_is_permanent_invalid_response_never_retried() -> None:
    provider, transport = make_hunter_provider(
        [(451, {"errors": [{"id": "claimed_email"}]})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_5xx_is_retryable_then_raises_provider_unavailable() -> None:
    provider, transport = make_hunter_provider(
        [(500, {}), (503, {})], settings_overrides={"hunter_max_transport_retries": 1}
    )
    with pytest.raises(EnrichmentProviderUnavailable):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 2


async def test_unknown_4xx_is_permanent_invalid_response() -> None:
    provider, transport = make_hunter_provider(
        [(418, {"errors": [{"id": "teapot"}]})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_unexpected_3xx_is_permanent_invalid_response() -> None:
    provider, transport = make_hunter_provider(
        [(301, {})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_non_json_response_body_is_invalid_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json", request=request)

    provider, _ = make_hunter_provider(handler=handler)
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")


async def test_non_dict_response_body_is_invalid_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps([1, 2, 3]), request=request)

    provider, _ = make_hunter_provider(handler=handler)
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")


async def test_error_classification_does_not_depend_on_errors_id_being_present() -> None:
    """HTTP-status-driven classification only — `errors[0].id` is best-effort
    telemetry, never load-bearing for classification."""
    provider, transport = make_hunter_provider(
        [(401, {})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentAuthError):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_transport_retry_then_succeeds() -> None:
    provider, transport = make_hunter_provider(
        [httpx.ConnectTimeout("boom"), (200, email_finder_response())],
        settings_overrides={"hunter_max_transport_retries": 1},
    )
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.matched is True
    assert transport.calls == 2
    assert len(result.telemetry) == 2


# --- budget ---------------------------------------------------------------


async def test_budget_denial_makes_zero_network_calls() -> None:
    class _AlwaysDenyBudget:
        async def reserve_call(self) -> bool:
            return False

    provider, transport = make_hunter_provider([], budget=_AlwaysDenyBudget())
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

    provider, transport = make_hunter_provider(
        [httpx.ConnectTimeout("boom"), (200, email_finder_response())],
        settings_overrides={"hunter_max_transport_retries": 1},
        budget=_CountingBudget(),
    )
    await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert calls_reserved == 1
    assert transport.calls == 2


async def test_whitespace_only_name_consumes_zero_budget_slots() -> None:
    calls_reserved = 0

    class _CountingBudget:
        async def reserve_call(self) -> bool:
            nonlocal calls_reserved
            calls_reserved += 1
            return True

    provider, transport = make_hunter_provider([], budget=_CountingBudget())
    await provider.enrich_person(_query(full_name="   "), ctx_key="r1:p1:contact_enrichment")
    assert calls_reserved == 0
    assert transport.calls == 0


# --- telemetry --------------------------------------------------------


async def test_unique_attempt_numbering_and_shared_call_group_id() -> None:
    from groundwork.providers.contact_base import EnrichmentAttemptKind

    provider, transport = make_hunter_provider(
        [httpx.ConnectTimeout("boom"), (200, email_finder_response())],
        settings_overrides={"hunter_max_transport_retries": 1},
    )
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert [t.attempt for t in result.telemetry] == [1, 2]
    assert result.telemetry[0].call_group_id == result.telemetry[1].call_group_id
    assert result.telemetry[0].attempt_kind == EnrichmentAttemptKind.INITIAL
    assert result.telemetry[1].attempt_kind == EnrichmentAttemptKind.TRANSPORT_RETRY


async def test_no_invented_cost_or_credits() -> None:
    provider, _ = make_hunter_provider([(200, email_finder_response())])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    for t in result.telemetry:
        assert t.credits_used is None
        assert t.cost_usd is None


async def test_input_and_output_digests_present() -> None:
    provider, _ = make_hunter_provider([(200, email_finder_response())])
    result = await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert result.telemetry[0].input_digest
    assert result.telemetry[0].output_digest


# --- provider purity ----------------------------------------------------


async def test_provider_purity_no_repository_or_sqlalchemy_imports() -> None:
    import ast
    import inspect

    from groundwork.providers.live import hunter_enrichment, hunter_runtime

    for module in (hunter_enrichment, hunter_runtime):
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

    from groundwork.providers.live import hunter_enrichment

    source = inspect.getsource(hunter_enrichment)
    assert "requests.get(" not in source
    assert "requests.post(" not in source
