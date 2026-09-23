"""v2.0.1 Live-Quality Hardening — deterministic persona-matching admission
gate. `LeadershipCandidate.is_persona_match` is LLM-authored and must never
by itself admit a contact — only `leader.title in persona_titles` may.
`persona_titles == []` means the Play named no qualifying buyer, so every
prospect resolves to UNAVAILABLE regardless of what the LLM claims.
"""

from __future__ import annotations

from groundwork.engine.steps.enrich import resolve_contact
from groundwork.models.enums import ContactVerification
from groundwork.models.schemas import LeadershipCandidate, ResearchFacts


def _facts(*leaders: LeadershipCandidate) -> ResearchFacts:
    from groundwork.models.schemas import CompanySeed

    company = CompanySeed(
        slug="acme", name="Acme Corp", domain="acme.com", industry="ai_infrastructure",
        size_band="51-200", employee_count=120,
    )
    return ResearchFacts(company=company, leadership=list(leaders))


def test_llm_claimed_persona_match_does_not_admit_a_title_not_targeted() -> None:
    # The real-world regression this closes: a Live extraction sets
    # `is_persona_match=True` for an "SVP Product" leader on a
    # revenue-persona play. The title alone must decide admission.
    leader = LeadershipCandidate(
        full_name="Jamie Rivera", title="SVP Product", is_persona_match=True,
        claim="Jamie Rivera is Acme Corp's SVP Product", source_ref="ref", evidence_ids=["ev-1"],
    )
    contact = resolve_contact("p-1", _facts(leader), persona_titles=["VP of Sales", "Head of Sales", "VP of Revenue"])
    assert contact.verification == ContactVerification.UNAVAILABLE
    assert contact.persona_match is False


def test_title_in_persona_titles_admits_regardless_of_llm_flag() -> None:
    leader = LeadershipCandidate(
        full_name="Priya Natarajan", title="VP of Sales", is_persona_match=False,
        claim="Priya Natarajan is Acme Corp's VP of Sales", source_ref="ref", evidence_ids=["ev-1"],
    )
    contact = resolve_contact("p-1", _facts(leader), persona_titles=["VP of Sales"])
    assert contact.verification == ContactVerification.VERIFIED
    assert contact.persona_match is True


def test_empty_persona_titles_means_no_qualifying_buyer() -> None:
    leader = LeadershipCandidate(
        full_name="Priya Natarajan", title="VP of Sales", is_persona_match=True,
        claim="Priya Natarajan is Acme Corp's VP of Sales", source_ref="ref", evidence_ids=["ev-1"],
    )
    contact = resolve_contact("p-1", _facts(leader), persona_titles=[])
    assert contact.verification == ContactVerification.UNAVAILABLE


def test_ungrounded_leader_never_admitted_even_with_matching_title() -> None:
    leader = LeadershipCandidate(
        full_name="Priya Natarajan", title="VP of Sales", is_persona_match=True,
        claim="Priya Natarajan is Acme Corp's VP of Sales", source_ref="ref", evidence_ids=[],
    )
    contact = resolve_contact("p-1", _facts(leader), persona_titles=["VP of Sales"])
    assert contact.verification == ContactVerification.UNAVAILABLE
