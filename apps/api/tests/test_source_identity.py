import random

from groundwork.domain.source_identity import (
    compute_content_sha256,
    evidence_id_for,
    group_occurrences,
    select_winners,
    source_identity,
)
from groundwork.models.schemas import SourceDocument


def _doc(**overrides) -> SourceDocument:
    defaults = dict(
        ref="ref-1", title="t", claim="c", text="some text", source_provider="demo_fixture",
    )
    defaults.update(overrides)
    return SourceDocument(**defaults)


def test_same_url_from_multiple_result_refs_collapses_to_one_group() -> None:
    a = _doc(ref="r1", url="https://example.com/page", canonical_url="https://example.com/page")
    b = _doc(ref="r2", url="https://example.com/page", canonical_url="https://example.com/page")
    c = _doc(ref="r3", url="https://example.com/page", canonical_url="https://example.com/page")
    groups = group_occurrences([a, b, c])
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_url_less_fixture_sources_remain_distinct_via_ref() -> None:
    a = _doc(ref="funding-note")
    b = _doc(ref="hiring-note")
    groups = group_occurrences([a, b])
    assert len(groups) == 2


def test_content_hash_duplicates_collapse_across_different_urls() -> None:
    a = _doc(ref="r1", url="https://mirror-a.example.com/p", canonical_url="https://mirror-a.example.com/p",
              content_sha256="abc123")
    b = _doc(ref="r2", url="https://mirror-b.example.com/p", canonical_url="https://mirror-b.example.com/p",
              content_sha256="abc123")
    groups = group_occurrences([a, b])
    assert len(groups) == 1


def test_deterministic_winner_under_shuffled_input() -> None:
    docs = [
        _doc(ref="r1", url="https://example.com/p", canonical_url="https://example.com/p", text="short", rank=3),
        _doc(ref="r2", url="https://example.com/p", canonical_url="https://example.com/p", text="a much longer body of text here", rank=1),
        _doc(ref="r3", url="https://example.com/p", canonical_url="https://example.com/p", text="medium length text", rank=2),
    ]
    winner_refs = set()
    rng = random.Random(1234)
    for _ in range(20):
        shuffled = docs[:]
        rng.shuffle(shuffled)
        winners = select_winners(shuffled)
        assert len(winners) == 1
        winner_refs.add(winners[0].ref)
    assert winner_refs == {"r2"}  # longest text wins regardless of shuffle


def test_winner_prefers_successful_status_over_longer_failed_text() -> None:
    failed = _doc(ref="r1", url="https://example.com/p", canonical_url="https://example.com/p",
                   text="a very long failed extraction body" * 5, status="failed")
    ok = _doc(ref="r2", url="https://example.com/p", canonical_url="https://example.com/p",
              text="short ok text", status="ok")
    winners = select_winners([failed, ok])
    assert len(winners) == 1
    assert winners[0].ref == "r2"


def test_duplicate_provider_results_do_not_alter_evidence_confidence() -> None:
    """Evidence is created only from winners — three occurrences of the same
    source must still contribute exactly one confidence value, not three."""
    docs = [
        _doc(ref="r1", url="https://example.com/p", canonical_url="https://example.com/p", confidence=0.9),
        _doc(ref="r2", url="https://example.com/p", canonical_url="https://example.com/p", confidence=0.9),
        _doc(ref="r3", url="https://example.com/p", canonical_url="https://example.com/p", confidence=0.9),
    ]
    winners = select_winners(docs)
    assert len(winners) == 1


def test_evidence_created_from_winner_only_deterministic_id() -> None:
    a = _doc(ref="r1", url="https://example.com/p", canonical_url="https://example.com/p", text="short")
    b = _doc(ref="r2", url="https://example.com/p", canonical_url="https://example.com/p", text="a longer body")
    winners = select_winners([a, b])
    assert len(winners) == 1
    eid1 = evidence_id_for("prospect-1", winners[0])
    eid2 = evidence_id_for("prospect-1", winners[0])
    assert eid1 == eid2  # deterministic/idempotent


def test_evidence_id_scoped_per_prospect() -> None:
    doc = _doc(ref="r1")
    a = evidence_id_for("prospect-a", doc)
    b = evidence_id_for("prospect-b", doc)
    assert a != b


def test_source_identity_prefers_canonical_url_over_ref() -> None:
    doc = _doc(ref="r1", url="https://example.com/p", canonical_url="https://example.com/p")
    assert source_identity(doc) == "url:https://example.com/p"


def test_source_identity_falls_back_to_ref_when_no_url() -> None:
    doc = _doc(ref="funding-note")
    assert source_identity(doc) == "ref:funding-note"


def test_compute_content_sha256_deterministic() -> None:
    assert compute_content_sha256("hello") == compute_content_sha256("hello")
    assert compute_content_sha256("hello") != compute_content_sha256("world")


def test_group_occurrences_empty_input() -> None:
    assert group_occurrences([]) == []
    assert select_winners([]) == []
