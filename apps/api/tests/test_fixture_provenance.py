"""No DEMO_FIXTURE evidence may ever carry an http(s) source_url — enforced
structurally by the Evidence model validator (§12), and re-asserted here
directly against the shipped fixture pack so a future fixture edit can't
regress it silently.
"""

import pytest
from pydantic import ValidationError

from groundwork.models.enums import EvidenceOrigin
from groundwork.models.schemas import Evidence
from groundwork.providers.demo.fixtures import load_fixture_pack


def test_no_fixture_source_has_an_http_url() -> None:
    pack = load_fixture_pack()
    for company in pack.companies:
        for source in company.sources:
            assert not source.snippet.startswith(("http://", "https://"))
            # The fixture schema doesn't even expose a source_url field —
            # sources only carry ref/title/claim/snippet.
            assert not hasattr(source, "source_url")


def test_every_fixture_row_has_a_title_and_snippet() -> None:
    pack = load_fixture_pack()
    for company in pack.companies:
        for source in company.sources:
            assert source.title.strip()
            assert source.snippet.strip()
            assert source.claim.strip()


def test_model_validator_rejects_fake_url_on_demo_fixture_evidence() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            id="ev-1",
            prospect_id="p-1",
            source_url="https://techcrunch.com/fake-article",
            source_provider="demo_fixture",
            title="t",
            claim="c",
            snippet="s",
            confidence=0.9,
            origin=EvidenceOrigin.DEMO_FIXTURE,
        )


def test_model_validator_rejects_fake_url_on_llm_inference_evidence() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            id="ev-1",
            prospect_id="p-1",
            source_url="https://example.com/inferred",
            source_provider="demo_llm",
            title="t",
            claim="c",
            snippet="s",
            confidence=0.5,
            origin=EvidenceOrigin.LLM_INFERENCE,
        )


def test_model_validator_requires_http_url_on_live_fetch_evidence() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            id="ev-1",
            prospect_id="p-1",
            source_url=None,
            source_provider="tavily",
            title="t",
            claim="c",
            snippet="s",
            confidence=0.9,
            origin=EvidenceOrigin.LIVE_FETCH,
        )


# =====================================================================
# v2 §N.3 — contact-enrichment fixture provenance
# =====================================================================


def test_no_fixture_enrichment_linkedin_url_is_a_real_external_url() -> None:
    pack = load_fixture_pack()
    checked = 0
    for company in pack.companies:
        if company.enrichment is None or company.enrichment.linkedin is None:
            continue
        checked += 1
        url = company.enrichment.linkedin.profile_url
        assert url.startswith("demo://linkedin/"), f"{company.slug}: {url!r} is not the demo:// grammar"
        assert not url.startswith(("http://", "https://"))
    assert checked > 0, "expected at least one fixture company to carry a LinkedIn enrichment observation"


def test_no_fixture_enrichment_email_is_flagged_as_a_groundwork_verdict() -> None:
    """Fixture email observations carry the PROVIDER's raw status word
    (`verified`, `catch_all`, ...), never a precomputed Groundwork
    `EmailVerificationState` value like `VERIFIED`/`RISKY` — those only ever
    exist after `domain/contact_identity.py::derive_email_channel` runs."""
    pack = load_fixture_pack()
    verdict_words = {"VERIFIED", "RISKY", "UNVERIFIABLE", "INVALID", "UNVERIFIED"}
    checked = 0
    for company in pack.companies:
        if company.enrichment is None or company.enrichment.email is None:
            continue
        checked += 1
        assert company.enrichment.email.provider_status not in verdict_words
    assert checked > 0


def test_fixture_enrichment_email_addresses_are_not_a_real_looking_free_provider() -> None:
    """Every fixture email address's domain matches the company's own
    fixture `domain` — never a real person's actual address at a real
    unrelated provider (gmail.com, etc.)."""
    pack = load_fixture_pack()
    checked = 0
    for company in pack.companies:
        if company.enrichment is None or company.enrichment.email is None:
            continue
        checked += 1
        address = company.enrichment.email.address
        assert address.endswith(f"@{company.domain}"), f"{company.slug}: {address!r} is not at its own fixture domain"
    assert checked > 0


def test_fixture_enrichment_is_an_observation_not_a_precomputed_resolution_state() -> None:
    """The fixture schema itself has no field for `LinkedInResolutionState`/
    `LinkedInIdentityState`/`EmailDiscoveryState` — structurally, a fixture
    author cannot ship a precomputed verdict even by accident."""
    pack = load_fixture_pack()
    for company in pack.companies:
        if company.enrichment is None:
            continue
        assert not hasattr(company.enrichment, "discovery_state")
        assert not hasattr(company.enrichment, "identity_match_state")
        assert not hasattr(company.enrichment, "verification_state")


def test_demo_fixture_evidence_with_no_url_is_valid() -> None:
    evidence = Evidence(
        id="ev-1",
        prospect_id="p-1",
        source_ref="demo://fixtures/northwind-labs/funding-note",
        source_provider="demo_fixture",
        title="t",
        claim="c",
        snippet="s",
        confidence=0.9,
        origin=EvidenceOrigin.DEMO_FIXTURE,
    )
    assert evidence.source_url is None
