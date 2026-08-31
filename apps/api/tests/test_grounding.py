from groundwork.domain.grounding import is_grounded, token_overlap, verify_claim_evidence
from groundwork.models.enums import EvidenceOrigin
from groundwork.models.schemas import Evidence


def _evidence(**overrides) -> Evidence:
    defaults = dict(
        id="ev-1",
        prospect_id="p-1",
        source_provider="demo_fixture",
        title="Funding note",
        claim="Northwind Labs raised a $42M Series B",
        snippet="Northwind Labs closes $42M Series B financing round to scale inference infrastructure",
        confidence=0.9,
        origin=EvidenceOrigin.DEMO_FIXTURE,
    )
    defaults.update(overrides)
    return Evidence(**defaults)


def test_token_overlap_full_match() -> None:
    assert token_overlap("Series B funding round", "Series B funding round announced") == 1.0


def test_token_overlap_no_match() -> None:
    assert token_overlap("acquired a competitor", "hired three new engineers") == 0.0


def test_is_grounded_true_for_supported_claim() -> None:
    evidence = _evidence()
    assert is_grounded(evidence.claim, evidence) is True


def test_is_grounded_false_for_unsupported_claim() -> None:
    evidence = _evidence(
        claim="Riverbend Analytics raised a Series A round",
        snippet="Riverbend Analytics is reportedly exploring new capital as it scales its platform",
    )
    assert is_grounded(evidence.claim, evidence) is False


def test_verify_claim_evidence_rejects_missing_id() -> None:
    assert verify_claim_evidence("claim", None, {}, "p-1") is False
    assert verify_claim_evidence("claim", "missing", {}, "p-1") is False


def test_verify_claim_evidence_rejects_cross_prospect_citation() -> None:
    evidence = _evidence(prospect_id="p-2")
    by_id = {evidence.id: evidence}
    assert verify_claim_evidence(evidence.claim, evidence.id, by_id, "p-1") is False


def test_verify_claim_evidence_accepts_valid_same_prospect_citation() -> None:
    evidence = _evidence(prospect_id="p-1")
    by_id = {evidence.id: evidence}
    assert verify_claim_evidence(evidence.claim, evidence.id, by_id, "p-1") is True
