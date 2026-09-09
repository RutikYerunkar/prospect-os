"""§3.7 — every observation->state mapping; `PROVIDER_ERROR` != `NOT_FOUND`;
an unmapped provider status fails closed; the full person/company/
combination matrix.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from groundwork.domain.contact_identity import (
    IDENTITY_MATCH_VERSION,
    CompanyMatch,
    PersonMatch,
    combine_identity,
    derive_email_channel,
    email_discovery_state_after_failed_call,
    linkedin_identifier_key,
    linkedin_resolution_state_after_failed_call,
    match_company,
    match_person,
)
from groundwork.models.enums import EmailDiscoveryState, EmailVerificationState, LinkedInIdentityState, LinkedInResolutionState
from groundwork.models.schemas import ProviderEmailObservation

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_version_is_v1():
    assert IDENTITY_MATCH_VERSION == "v1"


# =====================================================================
# person matching
# =====================================================================


class TestMatchPerson:
    def test_exact_match(self):
        assert match_person("Priya Natarajan", "Priya Natarajan") is PersonMatch.PERSON_MATCH

    def test_case_and_accent_insensitive_match(self):
        assert match_person("José García", "jose garcia") is PersonMatch.PERSON_MATCH

    def test_middle_name_ignored(self):
        assert match_person("Priya Kumar Natarajan", "Priya Natarajan") is PersonMatch.PERSON_MATCH

    def test_initial_match(self):
        assert match_person("P Natarajan", "Priya Natarajan") is PersonMatch.PERSON_MATCH
        assert match_person("Priya Natarajan", "P Natarajan") is PersonMatch.PERSON_MATCH

    def test_honorifics_and_suffixes_stripped(self):
        assert match_person("Dr. Priya Natarajan PhD", "Priya Natarajan") is PersonMatch.PERSON_MATCH

    def test_last_name_mismatch_is_conflict(self):
        assert match_person("Priya Natarajan", "Priya Sharma") is PersonMatch.PERSON_CONFLICT

    def test_nickname_is_conflict_not_fuzzy_matched(self):
        # No fuzzy matching, no edit distance — Jon vs John is a conflict,
        # by design (§3.7 Step 2).
        assert match_person("Jon Smith", "John Smith") is PersonMatch.PERSON_CONFLICT

    def test_first_initial_mismatch_is_conflict(self):
        assert match_person("Q Natarajan", "Priya Natarajan") is PersonMatch.PERSON_CONFLICT

    def test_single_token_name_is_unknown(self):
        assert match_person("Priya", "Priya Natarajan") is PersonMatch.PERSON_UNKNOWN

    def test_absent_name_is_unknown(self):
        assert match_person(None, "Priya Natarajan") is PersonMatch.PERSON_UNKNOWN
        assert match_person("Priya Natarajan", None) is PersonMatch.PERSON_UNKNOWN
        assert match_person(None, None) is PersonMatch.PERSON_UNKNOWN


# =====================================================================
# company matching
# =====================================================================


class TestMatchCompany:
    def test_domain_equality_preferred_and_matches(self):
        result = match_company(
            name_a="Northwind Labs",
            name_b="Totally Different Name Inc",
            domain_a="northwindlabs.com",
            domain_b="northwindlabs.com",
        )
        assert result is CompanyMatch.COMPANY_MATCH

    def test_domain_equality_preferred_and_conflicts_even_if_names_match(self):
        result = match_company(
            name_a="Northwind Labs",
            name_b="Northwind Labs",
            domain_a="northwindlabs.com",
            domain_b="totallydifferent.com",
        )
        assert result is CompanyMatch.COMPANY_CONFLICT

    def test_falls_through_to_name_matching_when_only_one_side_has_domain(self):
        # Apollo's asserted_company_domain is unverified/absent until V2-D —
        # the precedence order must still work correctly with it absent.
        result = match_company(
            name_a="Northwind Labs",
            name_b="Northwind Labs",
            domain_a="northwindlabs.com",
            domain_b=None,
        )
        assert result is CompanyMatch.COMPANY_MATCH

    def test_name_equality_with_corporate_suffix_stripped(self):
        assert match_company(name_a="Northwind Labs, Inc.", name_b="Northwind Labs") is CompanyMatch.COMPANY_MATCH

    def test_identity_bearing_words_never_stripped(self):
        # "labs"/"ai"/"technologies"/"systems" must survive — stripping them
        # would let two genuinely different companies collapse.
        assert match_company(name_a="Northwind Labs", name_b="Northwind") is CompanyMatch.COMPANY_CONFLICT
        assert match_company(name_a="Acme AI", name_b="Acme") is CompanyMatch.COMPANY_CONFLICT

    def test_name_mismatch_is_conflict(self):
        assert match_company(name_a="Northwind Labs", name_b="Sable Compute") is CompanyMatch.COMPANY_CONFLICT

    def test_both_absent_is_unknown(self):
        assert match_company(name_a=None, name_b=None) is CompanyMatch.COMPANY_UNKNOWN

    def test_one_name_absent_no_domain_is_unknown(self):
        assert match_company(name_a="Northwind Labs", name_b=None) is CompanyMatch.COMPANY_UNKNOWN


# =====================================================================
# §3.7 Step 4 — combination matrix (full table)
# =====================================================================


class TestCombineIdentity:
    def test_person_conflict_always_mismatch(self):
        for company in CompanyMatch:
            assert combine_identity(PersonMatch.PERSON_CONFLICT, company) is LinkedInIdentityState.MISMATCH

    def test_company_conflict_always_mismatch(self):
        for person in PersonMatch:
            assert combine_identity(person, CompanyMatch.COMPANY_CONFLICT) is LinkedInIdentityState.MISMATCH

    def test_match_match_is_strong(self):
        assert combine_identity(PersonMatch.PERSON_MATCH, CompanyMatch.COMPANY_MATCH) is LinkedInIdentityState.STRONG_MATCH

    def test_match_unknown_is_weak(self):
        assert combine_identity(PersonMatch.PERSON_MATCH, CompanyMatch.COMPANY_UNKNOWN) is LinkedInIdentityState.WEAK_MATCH

    def test_unknown_match_is_weak(self):
        assert combine_identity(PersonMatch.PERSON_UNKNOWN, CompanyMatch.COMPANY_MATCH) is LinkedInIdentityState.WEAK_MATCH

    def test_unknown_unknown_is_unknown(self):
        assert combine_identity(PersonMatch.PERSON_UNKNOWN, CompanyMatch.COMPANY_UNKNOWN) is LinkedInIdentityState.UNKNOWN

    def test_right_name_wrong_company_is_mismatch_not_weak(self):
        # A right name at the wrong company must not be actionable.
        assert (
            combine_identity(PersonMatch.PERSON_MATCH, CompanyMatch.COMPANY_CONFLICT)
            is LinkedInIdentityState.MISMATCH
        )


# =====================================================================
# email discovery/verification derivation
# =====================================================================


class TestDeriveEmailChannel:
    STATUS_MAP = {
        "verified": EmailVerificationState.VERIFIED,
        "valid": EmailVerificationState.VERIFIED,
        "risky": EmailVerificationState.RISKY,
        "invalid": EmailVerificationState.INVALID,
    }

    def test_matched_and_mapped_status_is_found_and_verified(self):
        obs = ProviderEmailObservation(address="priya@northwindlabs.com", provider_status="verified", observed_at=NOW)
        discovery, verification = derive_email_channel(obs, status_map=self.STATUS_MAP)
        assert discovery is EmailDiscoveryState.FOUND
        assert verification is EmailVerificationState.VERIFIED

    def test_status_matching_is_case_insensitive(self):
        obs = ProviderEmailObservation(address="priya@northwindlabs.com", provider_status="VERIFIED", observed_at=NOW)
        _, verification = derive_email_channel(obs, status_map=self.STATUS_MAP)
        assert verification is EmailVerificationState.VERIFIED

    def test_unmapped_status_fails_closed_to_unverified(self):
        obs = ProviderEmailObservation(
            address="priya@northwindlabs.com", provider_status="some-new-provider-word", observed_at=NOW
        )
        discovery, verification = derive_email_channel(obs, status_map=self.STATUS_MAP)
        assert discovery is EmailDiscoveryState.FOUND
        assert verification is EmailVerificationState.UNVERIFIED

    def test_missing_status_fails_closed_to_unverified(self):
        obs = ProviderEmailObservation(address="priya@northwindlabs.com", provider_status=None, observed_at=NOW)
        _, verification = derive_email_channel(obs, status_map=self.STATUS_MAP)
        assert verification is EmailVerificationState.UNVERIFIED

    def test_successful_call_no_address_is_not_found(self):
        obs = ProviderEmailObservation(address=None, provider_status=None, observed_at=NOW)
        discovery, verification = derive_email_channel(obs, status_map=self.STATUS_MAP)
        assert discovery is EmailDiscoveryState.NOT_FOUND
        assert verification is EmailVerificationState.UNVERIFIED

    def test_no_observation_is_not_found_not_provider_error(self):
        # derive_email_channel is only ever called from a SUCCESSFUL call
        # (§3.6); PROVIDER_ERROR is assigned by the caller on a failed call
        # via `email_discovery_state_after_failed_call`, never here.
        discovery, _ = derive_email_channel(None, status_map=self.STATUS_MAP)
        assert discovery is EmailDiscoveryState.NOT_FOUND
        assert discovery is not EmailDiscoveryState.PROVIDER_ERROR


class TestProviderErrorVsNotFound:
    """PROVIDER_ERROR (no successful observation has EVER been obtained) is
    a distinct state from NOT_FOUND (a successful call that found nothing)
    — §3.1/§3.6."""

    def test_provider_error_not_equal_to_not_found(self):
        assert EmailDiscoveryState.PROVIDER_ERROR != EmailDiscoveryState.NOT_FOUND
        assert LinkedInResolutionState.PROVIDER_ERROR != LinkedInResolutionState.NOT_FOUND

    def test_first_ever_failed_call_yields_provider_error(self):
        assert email_discovery_state_after_failed_call(None) is EmailDiscoveryState.PROVIDER_ERROR
        assert (
            email_discovery_state_after_failed_call(EmailDiscoveryState.NOT_ATTEMPTED)
            is EmailDiscoveryState.PROVIDER_ERROR
        )

    def test_failed_call_never_destroys_a_provider_backed_state(self):
        # Last-known-good (§3.6): a later timeout must never destroy a
        # previously derived, provider-backed channel state.
        for existing in (EmailDiscoveryState.FOUND, EmailDiscoveryState.NOT_FOUND):
            assert email_discovery_state_after_failed_call(existing) is existing

    def test_linkedin_analogue(self):
        assert linkedin_resolution_state_after_failed_call(None) is LinkedInResolutionState.PROVIDER_ERROR
        assert (
            linkedin_resolution_state_after_failed_call(LinkedInResolutionState.RESOLVED)
            is LinkedInResolutionState.RESOLVED
        )
        assert (
            linkedin_resolution_state_after_failed_call(LinkedInResolutionState.NOT_FOUND)
            is LinkedInResolutionState.NOT_FOUND
        )


@pytest.mark.parametrize(
    "person,company,expected",
    [
        (PersonMatch.PERSON_MATCH, CompanyMatch.COMPANY_MATCH, LinkedInIdentityState.STRONG_MATCH),
        (PersonMatch.PERSON_MATCH, CompanyMatch.COMPANY_UNKNOWN, LinkedInIdentityState.WEAK_MATCH),
        (PersonMatch.PERSON_MATCH, CompanyMatch.COMPANY_CONFLICT, LinkedInIdentityState.MISMATCH),
        (PersonMatch.PERSON_UNKNOWN, CompanyMatch.COMPANY_MATCH, LinkedInIdentityState.WEAK_MATCH),
        (PersonMatch.PERSON_UNKNOWN, CompanyMatch.COMPANY_UNKNOWN, LinkedInIdentityState.UNKNOWN),
        (PersonMatch.PERSON_UNKNOWN, CompanyMatch.COMPANY_CONFLICT, LinkedInIdentityState.MISMATCH),
        (PersonMatch.PERSON_CONFLICT, CompanyMatch.COMPANY_MATCH, LinkedInIdentityState.MISMATCH),
        (PersonMatch.PERSON_CONFLICT, CompanyMatch.COMPANY_UNKNOWN, LinkedInIdentityState.MISMATCH),
        (PersonMatch.PERSON_CONFLICT, CompanyMatch.COMPANY_CONFLICT, LinkedInIdentityState.MISMATCH),
    ],
)
def test_full_combination_matrix(person, company, expected):
    assert combine_identity(person, company) is expected


class TestLinkedInIdentifierKey:
    """v2 §V2-F — canonical comparison key used by `domain/review.py`'s
    rewritten `no_fabricated_contact` clause 3. Reuses the LIVE_PROVIDER
    grammar `validate_linkedin_identifier` already enforces — never a fresh
    ad-hoc parse."""

    def test_accepted_url_returns_a_key(self):
        key = linkedin_identifier_key("https://www.linkedin.com/in/jane-doe")
        assert key is not None

    def test_www_and_bare_host_produce_the_same_key(self):
        assert linkedin_identifier_key("https://www.linkedin.com/in/jane-doe") == linkedin_identifier_key(
            "https://linkedin.com/in/jane-doe"
        )

    def test_case_insensitive(self):
        assert linkedin_identifier_key("https://www.linkedin.com/in/Jane-Doe") == linkedin_identifier_key(
            "https://www.linkedin.com/IN/jane-doe".replace("IN", "in")
        )
        assert linkedin_identifier_key("https://WWW.LINKEDIN.COM/in/jane-doe") == linkedin_identifier_key(
            "https://www.linkedin.com/in/jane-doe"
        )

    def test_trailing_slash_does_not_change_the_key(self):
        assert linkedin_identifier_key("https://www.linkedin.com/in/jane-doe/") == linkedin_identifier_key(
            "https://www.linkedin.com/in/jane-doe"
        )

    def test_different_profile_produces_a_different_key(self):
        assert linkedin_identifier_key("https://www.linkedin.com/in/jane-doe") != linkedin_identifier_key(
            "https://www.linkedin.com/in/john-smith"
        )

    def test_demo_identifier_returns_none(self):
        assert linkedin_identifier_key("demo://linkedin/jane-doe") is None

    def test_malformed_url_returns_none(self):
        assert linkedin_identifier_key("https://not-linkedin.com/in/jane-doe") is None
        assert linkedin_identifier_key("http://www.linkedin.com/in/jane-doe") is None
