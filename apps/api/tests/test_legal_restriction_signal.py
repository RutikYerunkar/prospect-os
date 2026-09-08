"""V2-I-a — Hunter's HTTP 451 becomes a dedicated `EnrichmentLegalRestriction`
/ `EnrichmentAttemptStatus.LEGAL_RESTRICTION` signal, distinct from 404/422's
unchanged `EnrichmentInvalidResponse`. `errors[0].id` is captured on the
exception for audit/telemetry only — it is never load-bearing for
classification, verified here by producing an identical classification with
and without it. Apollo is untouched by this checkpoint (it has no 451
branch at all — see `providers/live/apollo_enrichment.py`).
"""

from __future__ import annotations

import pytest

from groundwork.models.enums import EnrichmentAttemptStatus
from groundwork.providers.contact_base import (
    EnrichmentInvalidResponse,
    EnrichmentLegalRestriction,
    PersonEnrichmentQuery,
)
from tests.live_hunter_helpers import make_hunter_provider


def _query() -> PersonEnrichmentQuery:
    return PersonEnrichmentQuery(
        full_name="Priya Natarajan",
        title="VP of Sales",
        company_name="Acme Robotics",
        company_domain="acme.example.com",
    )


async def test_451_raises_dedicated_legal_restriction_exception() -> None:
    provider, _transport = make_hunter_provider(
        [(451, {"errors": [{"id": "claimed_email"}]})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentLegalRestriction) as excinfo:
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert excinfo.value.telemetry[0].status == EnrichmentAttemptStatus.LEGAL_RESTRICTION
    assert excinfo.value.telemetry[0].http_status == 451


async def test_451_makes_exactly_one_provider_call_never_retried() -> None:
    provider, transport = make_hunter_provider(
        [(451, {"errors": [{"id": "claimed_email"}]})], settings_overrides={"hunter_max_transport_retries": 3}
    )
    with pytest.raises(EnrichmentLegalRestriction):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_451_without_errors_id_classifies_identically() -> None:
    """`errors[0].id` is audit-only — entirely absent, the raised exception
    type/status must be byte-identical to the case where it's present."""
    provider, transport = make_hunter_provider(
        [(451, {})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentLegalRestriction) as excinfo:
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1
    assert excinfo.value.telemetry[0].status == EnrichmentAttemptStatus.LEGAL_RESTRICTION
    assert excinfo.value.provider_code is None


async def test_provider_code_captured_as_audit_metadata_only() -> None:
    provider, _transport = make_hunter_provider(
        [(451, {"errors": [{"id": "claimed_email"}]})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentLegalRestriction) as excinfo:
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    # Carried on the exception purely so the repository can persist it as
    # audit metadata (`send_suppression_provider_code`) — the exception
    # TYPE and its telemetry `status` were already fixed by the HTTP status
    # code alone, before this value is ever read.
    assert excinfo.value.provider_code == "claimed_email"


async def test_404_is_unchanged_invalid_response() -> None:
    provider, transport = make_hunter_provider(
        [(404, {"errors": [{"id": "not_found"}]})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1


async def test_422_is_unchanged_invalid_response() -> None:
    provider, transport = make_hunter_provider(
        [(422, {"errors": [{"id": "unprocessable"}]})], settings_overrides={"hunter_max_transport_retries": 2}
    )
    with pytest.raises(EnrichmentInvalidResponse):
        await provider.enrich_person(_query(), ctx_key="r1:p1:contact_enrichment")
    assert transport.calls == 1
