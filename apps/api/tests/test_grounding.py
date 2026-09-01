from groundwork.domain.grounding import is_grounded, numeric_claim_supported, token_overlap, verify_claim_evidence
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


# --- H1 Phase 6: numeric provenance -----------------------------------------


def test_numeric_claim_supported_exact_digit_match() -> None:
    assert numeric_claim_supported("Northwind Labs has approximately 140 employees.", 140) is True


def test_numeric_claim_supported_thousands_separator() -> None:
    assert numeric_claim_supported("The company reports 1,200 employees worldwide.", 1200) is True


def test_numeric_claim_supported_k_shorthand() -> None:
    assert numeric_claim_supported("Headcount has grown to 12k employees.", 12000) is True


def test_numeric_claim_unsupported_vague_prose() -> None:
    assert numeric_claim_supported("The company has a large team of employees.", 140) is False
    assert numeric_claim_supported("Hundreds of employees work here.", 200) is False


def test_numeric_claim_unsupported_wrong_number() -> None:
    assert numeric_claim_supported("Northwind Labs has approximately 140 employees.", 150) is False


def test_numeric_claim_rejects_out_of_range_count() -> None:
    assert numeric_claim_supported("The filing lists 0 employees.", 0) is False
    assert numeric_claim_supported("The filing lists 50000000 employees.", 50_000_000) is False


def test_numeric_claim_empty_snippet() -> None:
    assert numeric_claim_supported("", 140) is False
