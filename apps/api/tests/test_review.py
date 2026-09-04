from groundwork.domain.review import run_checks
from groundwork.models.enums import Channel, EvidenceOrigin, LinkedInIdentityState, ReviewVerdict
from groundwork.models.schemas import (
    ClaimMapEntry,
    ContactChannelState,
    DimensionScore,
    Evidence,
    ICPScore,
    OutreachDraft,
)


def _evidence(eid: str, prospect_id: str = "p-1", claim: str = "Acme raised a Series B", snippet: str | None = None) -> Evidence:
    return Evidence(
        id=eid,
        prospect_id=prospect_id,
        source_provider="demo_fixture",
        title="note",
        claim=claim,
        snippet=snippet or claim,
        confidence=0.9,
        origin=EvidenceOrigin.DEMO_FIXTURE,
    )


def _score(unsupported_count: int = 0, confidence: float = 1.0) -> ICPScore:
    dims = [
        DimensionScore(name=f"dim{i}", raw=1.0, weight=0.1, contribution=0.1, evidence_ids=["e"], unsupported=i < unsupported_count)
        for i in range(8)
    ]
    return ICPScore(prospect_id="p-1", overall=90, dimensions=dims, confidence=confidence)


def _draft(subject: str = "Hello Acme", body: str = "We noticed Acme raised a Series B.", claim_map=None, channel: Channel = Channel.EMAIL) -> OutreachDraft:
    return OutreachDraft(prospect_id="p-1", channel=channel, subject=subject, body=body, claim_map=claim_map or [])


def _linkedin_draft(body: str = "Congrats on the Series B.", claim_map=None) -> OutreachDraft:
    return OutreachDraft(prospect_id="p-1", channel=Channel.LINKEDIN, subject=None, body=body, claim_map=claim_map or [])


def _email_channel(
    identifier: str | None = "jane@acme.com", derived_from_enrichment_id: str | None = "enr-1"
) -> ContactChannelState:
    return ContactChannelState(
        channel=Channel.EMAIL, identifier=identifier, discovery_state="FOUND", verification_state="VERIFIED",
        derived_from_enrichment_id=derived_from_enrichment_id,
    )


def _linkedin_channel(
    identifier: str | None = "https://www.linkedin.com/in/jane-doe",
    identity_match_state: str = LinkedInIdentityState.STRONG_MATCH.value,
    derived_from_enrichment_id: str | None = "enr-1",
    discovery_state: str = "RESOLVED",
) -> ContactChannelState:
    return ContactChannelState(
        channel=Channel.LINKEDIN, identifier=identifier, discovery_state=discovery_state,
        identity_match_state=identity_match_state, derived_from_enrichment_id=derived_from_enrichment_id,
    )


def _base_kwargs(**overrides):
    kwargs = dict(
        prospect_id="p-1",
        evidence=[_evidence("ev-1")],
        drafts=[_draft(claim_map=[ClaimMapEntry(sentence="Acme raised a Series B", evidence_ids=["ev-1"])])],
        contact_channels=[],
        score=_score(),
        dedupe_key="domain:acme.com",
        other_dedupe_keys=set(),
        other_company_identifiers=set(),
        min_confidence=0.6,
    )
    kwargs.update(overrides)
    return kwargs


def test_clean_prospect_passes() -> None:
    result = run_checks(**_base_kwargs())
    assert result.verdict == ReviewVerdict.PASS
    assert all(c.passed for c in result.checks)
    assert len(result.checks) == 7


def test_claim_grounding_fails_on_nonexistent_evidence_id() -> None:
    draft = _draft(claim_map=[ClaimMapEntry(sentence="Acme raised a Series B", evidence_ids=["missing"])])
    result = run_checks(**_base_kwargs(drafts=[draft]))
    assert result.verdict == ReviewVerdict.FAIL
    check = next(c for c in result.checks if c.id == "claim_grounding")
    assert check.passed is False


def test_claim_grounding_fails_on_cross_prospect_evidence() -> None:
    foreign_evidence = _evidence("ev-2", prospect_id="p-2")
    draft = _draft(claim_map=[ClaimMapEntry(sentence="Acme raised a Series B", evidence_ids=["ev-2"])])
    result = run_checks(**_base_kwargs(evidence=[foreign_evidence], drafts=[draft]))
    check = next(c for c in result.checks if c.id == "claim_grounding")
    assert check.passed is False
    assert result.verdict == ReviewVerdict.FAIL


def test_claim_grounding_fails_on_unsupported_snippet() -> None:
    weak_evidence = _evidence("ev-3", claim="funding", snippet="totally unrelated text about hiring")
    draft = _draft(claim_map=[ClaimMapEntry(sentence="Acme raised a Series B", evidence_ids=["ev-3"])])
    result = run_checks(**_base_kwargs(evidence=[weak_evidence], drafts=[draft]))
    check = next(c for c in result.checks if c.id == "claim_grounding")
    assert check.passed is False


# --- v2 §V2-F: `no_fabricated_contact` rewrite — three deterministic
# clauses, provenance-based, never reading `contact.verification`. See
# `domain/review.py::_no_fabricated_contact`.


def test_no_fabricated_contact_empty_contact_channels_passes() -> None:
    result = run_checks(**_base_kwargs(contact_channels=[]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is True


def test_no_fabricated_contact_own_provider_observed_email_does_not_self_trip() -> None:
    draft = _draft(body="Reach me or my colleague at jane@acme.com for details.")
    result = run_checks(**_base_kwargs(drafts=[draft], contact_channels=[_email_channel(identifier="jane@acme.com")]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is True


def test_no_fabricated_contact_own_email_case_and_dot_normalized() -> None:
    # Draft echoes the identifier with different casing — normalization must
    # tolerate this, not naive string comparison.
    draft = _draft(body="Reach me at Jane@Acme.com for details.")
    result = run_checks(**_base_kwargs(drafts=[draft], contact_channels=[_email_channel(identifier="jane@acme.com")]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is True


def test_no_fabricated_contact_foreign_email_hard_fails() -> None:
    draft = _draft(body="You can also try someone@othercompany.com if I don't respond.")
    result = run_checks(**_base_kwargs(drafts=[draft], contact_channels=[_email_channel(identifier="jane@acme.com")]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is False
    assert result.verdict == ReviewVerdict.FAIL


def test_no_fabricated_contact_unbacked_identifier_hard_fails() -> None:
    # identifier present but no provider observation behind it (clause 1) —
    # fails even though it never appears in any draft.
    channel = _email_channel(identifier="jane@acme.com", derived_from_enrichment_id=None)
    result = run_checks(**_base_kwargs(contact_channels=[channel]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is False


def test_no_fabricated_contact_linkedin_mismatch_hard_fails() -> None:
    channel = _linkedin_channel(identity_match_state=LinkedInIdentityState.MISMATCH.value)
    result = run_checks(**_base_kwargs(contact_channels=[channel]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is False
    assert result.verdict == ReviewVerdict.FAIL


def test_no_fabricated_contact_linkedin_strong_match_does_not_trigger_mismatch_clause() -> None:
    channel = _linkedin_channel(identity_match_state=LinkedInIdentityState.STRONG_MATCH.value)
    result = run_checks(**_base_kwargs(contact_channels=[channel]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is True


def test_no_fabricated_contact_linkedin_weak_match_does_not_trigger_mismatch_clause() -> None:
    channel = _linkedin_channel(identity_match_state=LinkedInIdentityState.WEAK_MATCH.value)
    result = run_checks(**_base_kwargs(contact_channels=[channel]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is True


def test_no_fabricated_contact_linkedin_null_identity_state_does_not_trigger_mismatch_clause() -> None:
    channel = _linkedin_channel(identity_match_state=None, identifier=None, discovery_state="NOT_FOUND", derived_from_enrichment_id=None)
    result = run_checks(**_base_kwargs(contact_channels=[channel]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is True


def test_no_fabricated_contact_own_linkedin_identifier_does_not_self_trip() -> None:
    draft = _linkedin_draft(body="Connect with me here: https://www.linkedin.com/in/jane-doe")
    channel = _linkedin_channel(identifier="https://www.linkedin.com/in/jane-doe")
    result = run_checks(**_base_kwargs(drafts=[draft], contact_channels=[channel]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is True


def test_no_fabricated_contact_foreign_linkedin_url_hard_fails() -> None:
    draft = _linkedin_draft(body="Or find my colleague at https://www.linkedin.com/in/someone-else")
    channel = _linkedin_channel(identifier="https://www.linkedin.com/in/jane-doe")
    result = run_checks(**_base_kwargs(drafts=[draft], contact_channels=[channel]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is False


def test_no_fabricated_contact_demo_identifier_never_leaks_as_url_and_never_backs_a_url() -> None:
    # A demo:// identifier can never satisfy clause 3 (it never passes the
    # LIVE_PROVIDER grammar `linkedin_identifier_key` requires) — but it also
    # never appears as an https:// token in draft text, so this is a no-op.
    channel = _linkedin_channel(identifier="demo://linkedin/jane-doe", identity_match_state=LinkedInIdentityState.STRONG_MATCH.value)
    result = run_checks(**_base_kwargs(contact_channels=[channel]))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is True


def test_cross_prospect_leak_detects_other_company_name() -> None:
    draft = _draft(body="We noticed Acme raised a Series B, unlike Initech.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft], other_company_identifiers={"Initech"}))
    check = next(c for c in result.checks if c.id == "cross_prospect_leak")
    assert check.passed is False


def test_cross_prospect_leak_detects_other_company_domain() -> None:
    draft = _draft(body="See more at initech.com for context.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft], other_company_identifiers={"initech.com"}))
    check = next(c for c in result.checks if c.id == "cross_prospect_leak")
    assert check.passed is False


# --- v2 §V2-F bug fix: a `None` subject (a LinkedIn draft) must never be
# interpolated as the literal text "None" when scanned.


def test_cross_prospect_leak_null_subject_never_becomes_literal_none() -> None:
    draft = _linkedin_draft(body="Congrats on the round, no other company mentioned here.")
    result = run_checks(**_base_kwargs(drafts=[draft], other_company_identifiers={"None"}))
    check = next(c for c in result.checks if c.id == "cross_prospect_leak")
    assert check.passed is True


def test_cross_prospect_leak_null_subject_still_catches_real_leak_in_body() -> None:
    draft = _linkedin_draft(body="Unlike Initech, you all are moving fast.")
    result = run_checks(**_base_kwargs(drafts=[draft], other_company_identifiers={"Initech"}))
    check = next(c for c in result.checks if c.id == "cross_prospect_leak")
    assert check.passed is False


# --- H1 Bug B regression: real short company names must not hard-fail
# merely because their character sequence occurs inside an unrelated word.
# The pre-H1 implementation used a plain substring check
# (`identifier.lower() in text`), which is a false positive machine —
# "Ramp" matches inside "cramping", "Box" matches inside "mailbox", "Arc"
# matches inside "March". `_identifier_pattern`'s word-boundary regex must
# not match any of these while still catching the real reference.


def test_cross_prospect_leak_short_name_no_false_positive_ramp() -> None:
    draft = _draft(body="Congrats on the momentum — the team is really cramping our style.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft], other_company_identifiers={"Ramp"}))
    check = next(c for c in result.checks if c.id == "cross_prospect_leak")
    assert check.passed is True


def test_cross_prospect_leak_short_name_no_false_positive_box() -> None:
    draft = _draft(body="Ping me and I'll check my mailbox for the reply.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft], other_company_identifiers={"Box"}))
    check = next(c for c in result.checks if c.id == "cross_prospect_leak")
    assert check.passed is True


def test_cross_prospect_leak_short_name_no_false_positive_arc() -> None:
    draft = _draft(body="We spoke back in March about this opportunity.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft], other_company_identifiers={"Arc"}))
    check = next(c for c in result.checks if c.id == "cross_prospect_leak")
    assert check.passed is True


def test_cross_prospect_leak_short_name_real_reference_still_caught() -> None:
    draft = _draft(body="Unlike Ramp, Acme raised a Series B this quarter.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft], other_company_identifiers={"Ramp"}))
    check = next(c for c in result.checks if c.id == "cross_prospect_leak")
    assert check.passed is False


def test_cross_prospect_leak_case_insensitive_word_boundary() -> None:
    draft = _draft(body="Our friends over at BOX have a similar model.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft], other_company_identifiers={"Box"}))
    check = next(c for c in result.checks if c.id == "cross_prospect_leak")
    assert check.passed is False


def test_no_placeholders_detects_template_tokens() -> None:
    draft = _draft(body="Hi {{first_name}}, congrats on the round.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is False


# --- Production bug regression: the first real Live Mode run produced outreach
# ending in "Best,\n[Your Name]" and the deterministic no_placeholders check
# reported PASS. Root cause: the pattern set only matched the literal string
# "[company]", not the general shape of a bracket/brace/angle-bracket
# placeholder. See `_PLACEHOLDER_PATTERNS` in `domain/review.py`.


def test_no_placeholders_catches_exact_production_case_your_name() -> None:
    draft = _draft(body="Thanks for your time.\n\nBest,\n[Your Name]", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is False
    assert result.verdict == ReviewVerdict.FAIL


def test_no_placeholders_detects_bracket_company_placeholder() -> None:
    draft = _draft(subject="Quick note for [Company]", body="Hi there, loved what [Company] is building.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is False


def test_no_placeholders_detects_bracket_first_name_placeholder() -> None:
    draft = _draft(body="Hi [First Name], congrats on the round.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is False


def test_no_placeholders_detects_double_brace_with_spaces() -> None:
    draft = _draft(body="Hi {{ company }}, congrats on the round.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is False


def test_no_placeholders_detects_double_brace_name() -> None:
    draft = _draft(body="Hi {{name}}, congrats on the round.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is False


def test_no_placeholders_detects_angle_bracket_placeholder() -> None:
    draft = _draft(body="Thanks,\n<YOUR_NAME>", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is False


def test_no_placeholders_detects_todo() -> None:
    draft = _draft(body="TODO: personalize this before sending.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is False


def test_no_placeholders_clean_ordinary_outreach_passes() -> None:
    draft = _draft(
        subject="Congrats on the Series B, Acme",
        body=(
            "Hi Jane,\n\n"
            "Congrats on the momentum at Acme — Acme raised a Series B.\n\n"
            "Worth a quick conversation about how we could support your team?\n\n"
            "Best,\nThe Groundwork Team"
        ),
        claim_map=[],
    )
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is True


def test_no_placeholders_no_false_positive_on_numeric_bracket_citation() -> None:
    draft = _draft(body="Great progress this year [1] — worth a quick chat.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is True


# --- v2 §V2-F: `no_placeholders` is channel-aware — the empty-subject
# clause applies only to EMAIL; a LinkedIn draft's `subject is None` is its
# normal, complete shape. Body-empty stays universal for every channel.


def test_no_placeholders_email_empty_subject_still_fails() -> None:
    draft = _draft(subject="", body="Hi Jane, congrats on the round.", claim_map=[])
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is False


def test_no_placeholders_linkedin_null_subject_passes() -> None:
    draft = _linkedin_draft(body="Hi Jane, congrats on the round — worth a quick chat?")
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is True


def test_no_placeholders_linkedin_empty_body_fails() -> None:
    draft = _linkedin_draft(body="   ")
    result = run_checks(**_base_kwargs(drafts=[draft]))
    check = next(c for c in result.checks if c.id == "no_placeholders")
    assert check.passed is False


def test_duplicate_account_detects_key_collision() -> None:
    result = run_checks(**_base_kwargs(other_dedupe_keys={"domain:acme.com"}))
    check = next(c for c in result.checks if c.id == "duplicate_account")
    assert check.passed is False


def test_score_support_soft_fails_with_more_than_two_unsupported() -> None:
    result = run_checks(**_base_kwargs(score=_score(unsupported_count=3)))
    check = next(c for c in result.checks if c.id == "score_support")
    assert check.passed is False
    assert result.verdict == ReviewVerdict.NEEDS_REVIEW


def test_confidence_floor_soft_fails_below_minimum() -> None:
    result = run_checks(**_base_kwargs(score=_score(confidence=0.3)))
    check = next(c for c in result.checks if c.id == "confidence_floor")
    assert check.passed is False
    assert result.verdict == ReviewVerdict.NEEDS_REVIEW


def test_hard_failure_outranks_soft_failure_in_verdict() -> None:
    channel = _email_channel(identifier="jane@acme.com", derived_from_enrichment_id=None)
    result = run_checks(**_base_kwargs(contact_channels=[channel], score=_score(confidence=0.3)))
    assert result.verdict == ReviewVerdict.FAIL
