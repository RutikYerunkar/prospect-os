from groundwork.domain.review import run_checks
from groundwork.models.enums import ContactVerification, EvidenceOrigin, ReviewVerdict
from groundwork.models.schemas import (
    ClaimMapEntry,
    Contact,
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


def _draft(subject: str = "Hello Acme", body: str = "We noticed Acme raised a Series B.", claim_map=None) -> OutreachDraft:
    return OutreachDraft(prospect_id="p-1", subject=subject, body=body, claim_map=claim_map or [])


def _base_kwargs(**overrides):
    kwargs = dict(
        prospect_id="p-1",
        evidence=[_evidence("ev-1")],
        drafts=[_draft(claim_map=[ClaimMapEntry(sentence="Acme raised a Series B", evidence_ids=["ev-1"])])],
        contact=Contact(prospect_id="p-1", verification=ContactVerification.VERIFIED, evidence_ids=["ev-c"]),
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


def test_no_fabricated_contact_fails_when_unverified_with_email() -> None:
    contact = Contact(prospect_id="p-1", email="jane@acme.com", verification=ContactVerification.PERSONA_ONLY)
    result = run_checks(**_base_kwargs(contact=contact))
    check = next(c for c in result.checks if c.id == "no_fabricated_contact")
    assert check.passed is False
    assert result.verdict == ReviewVerdict.FAIL


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
    contact = Contact(prospect_id="p-1", email="jane@acme.com", verification=ContactVerification.PERSONA_ONLY)
    result = run_checks(**_base_kwargs(contact=contact, score=_score(confidence=0.3)))
    assert result.verdict == ReviewVerdict.FAIL
