"""§3.7 Step 0 — origin-aware LinkedIn identifier grammar (the four required
proofs from `docs/V2_IMPLEMENTATION_PLAN.md` Part 13/V2-B), plus the two
`ContactEnrichment` model validators that enforce the same grammar a second
time at the persistence boundary — "secrets are scrubbed twice, not once."
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from groundwork.domain.contact_identity import (
    IDENTIFIER_GRAMMAR_VERSION,
    IdentifierVerdict,
    derive_linkedin_channel,
    validate_linkedin_identifier,
)
from groundwork.models.enums import EnrichmentOrigin, LinkedInIdentityState, LinkedInResolutionState
from groundwork.models.schemas import ContactEnrichment, ProviderLinkedInObservation

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_version_is_v1():
    assert IDENTIFIER_GRAMMAR_VERSION == "v1"


def _enrichment(**overrides) -> dict:
    base = dict(
        prospect_id="p1",
        provider="apollo",
        call_group_id="g1",
        matched=True,
        origin=EnrichmentOrigin.DEMO_FIXTURE,
        observed_at=NOW,
        raw_digest="deadbeef",
    )
    base.update(overrides)
    return base


class TestFourRequiredProofs:
    def test_a_demo_fixture_demo_url_derives_resolved_and_strong_match(self):
        """(a) a DEMO_FIXTURE `demo://linkedin/priya-natarajan` derives
        RESOLVED and, with matching name+domain, STRONG_MATCH."""
        assert validate_linkedin_identifier(
            "demo://linkedin/priya-natarajan", origin=EnrichmentOrigin.DEMO_FIXTURE
        ) is IdentifierVerdict.ACCEPTED

        obs = ProviderLinkedInObservation(
            profile_url="demo://linkedin/priya-natarajan",
            asserted_full_name="Priya Natarajan",
            asserted_company_name="Northwind Labs",
            asserted_company_domain="northwindlabs.com",
            observed_at=NOW,
        )
        resolution, identity = derive_linkedin_channel(
            obs,
            origin=EnrichmentOrigin.DEMO_FIXTURE,
            grounded_full_name="Priya Natarajan",
            grounded_company_name="Northwind Labs",
            grounded_company_domain="northwindlabs.com",
        )
        assert resolution is LinkedInResolutionState.RESOLVED
        assert identity is LinkedInIdentityState.STRONG_MATCH

    def test_b_demo_fixture_real_url_rejected_at_validator_and_derivation(self):
        """(b) a DEMO_FIXTURE row carrying `https://linkedin.com/in/x` is
        rejected at the validator AND derives NOT_FOUND."""
        with pytest.raises(ValueError):
            ContactEnrichment(**_enrichment(
                origin=EnrichmentOrigin.DEMO_FIXTURE,
                linkedin_url="https://linkedin.com/in/x",
            ))

        assert validate_linkedin_identifier(
            "https://linkedin.com/in/x", origin=EnrichmentOrigin.DEMO_FIXTURE
        ) is IdentifierVerdict.REJECTED

        obs = ProviderLinkedInObservation(profile_url="https://linkedin.com/in/x", observed_at=NOW)
        resolution, identity = derive_linkedin_channel(
            obs,
            origin=EnrichmentOrigin.DEMO_FIXTURE,
            grounded_full_name=None,
            grounded_company_name=None,
            grounded_company_domain=None,
        )
        assert resolution is LinkedInResolutionState.NOT_FOUND
        assert identity is LinkedInIdentityState.UNKNOWN

    def test_c_live_provider_demo_url_rejected_at_validator_and_derivation(self):
        """(c) a LIVE_PROVIDER row carrying `demo://...` is rejected at the
        validator AND derives NOT_FOUND."""
        with pytest.raises(ValueError):
            ContactEnrichment(**_enrichment(
                origin=EnrichmentOrigin.LIVE_PROVIDER,
                linkedin_url="demo://linkedin/priya-natarajan",
            ))

        assert validate_linkedin_identifier(
            "demo://linkedin/priya-natarajan", origin=EnrichmentOrigin.LIVE_PROVIDER
        ) is IdentifierVerdict.REJECTED

        obs = ProviderLinkedInObservation(profile_url="demo://linkedin/priya-natarajan", observed_at=NOW)
        resolution, identity = derive_linkedin_channel(
            obs,
            origin=EnrichmentOrigin.LIVE_PROVIDER,
            grounded_full_name=None,
            grounded_company_name=None,
            grounded_company_domain=None,
        )
        assert resolution is LinkedInResolutionState.NOT_FOUND
        assert identity is LinkedInIdentityState.UNKNOWN

    @pytest.mark.parametrize(
        "raw",
        [
            "http://linkedin.com/in/priya",  # not https
            "https://notlinkedin.com/in/priya",  # wrong registrable domain
            "https://linkedin.com.evil.com/in/priya",  # lookalike host
            "https://linkedin.com/notin/priya",  # wrong path shape
            "https://linkedin.com/in/",  # empty id
            "https://user:pass@linkedin.com/in/priya",  # userinfo
            "https://linkedin.com:8443/in/priya",  # explicit port
            "https://linkedin.com/in/priya#section",  # fragment
            "not-a-url-at-all",
        ],
    )
    def test_d_malformed_or_non_linkedin_live_urls_fail_closed_to_not_found(self, raw):
        """(d) malformed/non-LinkedIn LIVE URLs all fail closed to NOT_FOUND."""
        assert validate_linkedin_identifier(raw, origin=EnrichmentOrigin.LIVE_PROVIDER) is IdentifierVerdict.REJECTED

        obs = ProviderLinkedInObservation(profile_url=raw, observed_at=NOW)
        resolution, identity = derive_linkedin_channel(
            obs,
            origin=EnrichmentOrigin.LIVE_PROVIDER,
            grounded_full_name=None,
            grounded_company_name=None,
            grounded_company_domain=None,
        )
        assert resolution is LinkedInResolutionState.NOT_FOUND
        assert identity is LinkedInIdentityState.UNKNOWN


class TestGrammarsRejectEachOthersShape:
    def test_absent_identifier_is_absent_not_rejected(self):
        assert validate_linkedin_identifier(None, origin=EnrichmentOrigin.DEMO_FIXTURE) is IdentifierVerdict.ABSENT
        assert validate_linkedin_identifier(None, origin=EnrichmentOrigin.LIVE_PROVIDER) is IdentifierVerdict.ABSENT

    def test_valid_live_url_is_accepted(self):
        assert validate_linkedin_identifier(
            "https://www.linkedin.com/in/priya-natarajan-42/", origin=EnrichmentOrigin.LIVE_PROVIDER
        ) is IdentifierVerdict.ACCEPTED

    def test_bare_linkedin_com_host_accepted_not_only_www(self):
        assert validate_linkedin_identifier(
            "https://linkedin.com/in/priya-natarajan", origin=EnrichmentOrigin.LIVE_PROVIDER
        ) is IdentifierVerdict.ACCEPTED

    @pytest.mark.parametrize(
        "raw",
        [
            "demo://linkedin/UPPERCASE",  # uppercase not allowed
            "demo://linkedin/-leading-hyphen",
            "demo://notlinkedin/slug",
            "demo://linkedin/",
        ],
    )
    def test_malformed_demo_identifiers_rejected(self, raw):
        assert validate_linkedin_identifier(raw, origin=EnrichmentOrigin.DEMO_FIXTURE) is IdentifierVerdict.REJECTED


class TestContactEnrichmentValidatorsAllowValidRows:
    def test_demo_fixture_row_with_valid_demo_url_persists(self):
        row = ContactEnrichment(**_enrichment(
            origin=EnrichmentOrigin.DEMO_FIXTURE,
            linkedin_url="demo://linkedin/priya-natarajan",
        ))
        assert row.linkedin_url == "demo://linkedin/priya-natarajan"

    def test_live_provider_row_with_valid_https_url_persists(self):
        row = ContactEnrichment(**_enrichment(
            origin=EnrichmentOrigin.LIVE_PROVIDER,
            linkedin_url="https://www.linkedin.com/in/priya-natarajan",
        ))
        assert row.linkedin_url == "https://www.linkedin.com/in/priya-natarajan"

    def test_row_with_no_linkedin_identifier_persists_for_either_origin(self):
        ContactEnrichment(**_enrichment(origin=EnrichmentOrigin.DEMO_FIXTURE, linkedin_url=None))
        ContactEnrichment(**_enrichment(origin=EnrichmentOrigin.LIVE_PROVIDER, linkedin_url=None))
