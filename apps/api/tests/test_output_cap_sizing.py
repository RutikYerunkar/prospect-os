"""Phase 4: `LLM_MAX_OUTPUT_TOKENS` must be measurement-selected, not an
arbitrary constant. This test encodes the measurement from
`docs/PROGRESS.md` (Checkpoint G Phase 4): the largest observed operation
(`research_extraction`, worst-case padded facts across every fixture
company) serializes to ~3.05KB. At a conservative ~3.5 chars/token, that's
under 900 visible tokens — the configured cap must leave comfortable
headroom above that for real-model verbosity plus low-effort reasoning
tokens (which count toward the same budget), without silently regressing to
an unreasonably small number either.
"""

from __future__ import annotations

import json
from datetime import date

from groundwork.config import settings
from groundwork.models.llm_io import ObjectiveParseOutput, PersonalizationOutput, ResearchExtractionOutput, ScoreExplanationOutput
from groundwork.models.schemas import ClaimMapEntry, FundingEvent, HiringRole, LeadershipCandidate, ResearchFacts, TechMention
from groundwork.providers.demo.fixtures import load_fixture_pack

CHARS_PER_TOKEN_ESTIMATE = 3.5  # conservative (real GPT tokenization averages ~4)


def _measure(model) -> int:
    return len(json.dumps(model.model_dump(mode="json")))


def _worst_case_research_extraction_chars() -> int:
    pack = load_fixture_pack()
    worst = 0
    for c in pack.companies:
        facts = ResearchFacts(
            company=c.to_company_seed(),
            funding_events=[
                FundingEvent(stage=f.stage, amount_usd=f.amount_usd, announced_at=date.today(), claim="x" * 200, source_ref=f.source_ref, evidence_ids=["x" * 36])
                for f in c.funding_events
            ],
            hiring_roles=[
                HiringRole(title=h.title, is_gtm=h.is_gtm, posted_at=date.today(), claim="x" * 200, source_ref=h.source_ref, evidence_ids=["x" * 36])
                for h in c.hiring_roles
            ],
            tech_mentions=[
                TechMention(name=t.name, claim="x" * 200, source_ref=t.source_ref, evidence_ids=["x" * 36]) for t in c.tech_mentions
            ],
            leadership=[
                LeadershipCandidate(full_name=leader.full_name, title=leader.title, is_persona_match=leader.is_persona_match, claim="x" * 200, source_ref=leader.source_ref, evidence_ids=["x" * 36])
                for leader in c.leadership
            ],
        )
        worst = max(worst, _measure(ResearchExtractionOutput(facts=facts)))
    return worst


def test_measured_worst_case_sizes_fit_cap_with_headroom():
    research_chars = _worst_case_research_extraction_chars()
    score_chars = _measure(ScoreExplanationOutput(explanation="x" * 500))
    personalization_chars = _measure(
        PersonalizationOutput(subject="x" * 100, body="x" * 1500, claim_map=[ClaimMapEntry(sentence="x" * 200, evidence_ids=["x" * 36])] * 4)
    )
    objective_chars = _measure(
        ObjectiveParseOutput(
            target_industries=["x" * 30] * 8, excluded_industries=["x" * 30] * 8, target_funding_stages=["x" * 20] * 5,
            target_technologies=["x" * 30] * 10, persona_titles=["x" * 40] * 5,
            size_band_min=1, size_band_max=5000, min_score=80, min_confidence=0.9,
        )
    )

    largest_chars = max(research_chars, score_chars, personalization_chars, objective_chars)
    assert largest_chars == research_chars, "research_extraction should remain the largest measured operation"

    largest_visible_tokens = largest_chars / CHARS_PER_TOKEN_ESTIMATE
    cap = settings.llm_max_output_tokens

    # The cap must comfortably exceed the largest measured visible-output
    # estimate (real headroom, not a razor's edge)...
    assert cap > largest_visible_tokens * 1.5, (
        f"llm_max_output_tokens={cap} leaves too little headroom over the measured "
        f"worst case (~{largest_visible_tokens:.0f} tokens)"
    )
    # ...but the cap is measurement-selected, not an arbitrary round number
    # far beyond what any of the four operations could plausibly need —
    # this is the "do not preserve 3000 merely because the plan mentioned
    # it" requirement made a regression test.
    assert cap < largest_visible_tokens * 6, (
        f"llm_max_output_tokens={cap} is far larger than any measured operation needs "
        f"(~{largest_visible_tokens:.0f} tokens) — re-justify or shrink it"
    )
