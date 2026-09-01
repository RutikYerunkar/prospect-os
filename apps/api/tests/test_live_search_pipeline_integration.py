"""H2 Phase 22 — full `execute_run()` integration through a real (scripted)
`TavilySearchProvider`: discovery -> dedupe -> research -> signals -> enrich
-> score -> contact -> review -> finalize, for one real-shaped prospect.
Proves the whole wiring (engine/runner.py branch, providers/registry.py,
company origin, search_calls/source_documents reconciliation) works
together, not just each piece in isolation. No network calls.
"""

from __future__ import annotations

import re

from groundwork.engine.budget import DEMO_BUDGET
from groundwork.engine.discovery import DiscoveryBounds
from groundwork.engine.runner import Repos, execute_run
from groundwork.evaluation.metrics import compute_run_evaluation
from groundwork.models.enums import EvidenceOrigin
from groundwork.models.llm_io import (
    DiscoveryCandidate,
    DiscoveryExtractionOutput,
    ResearchExtractionOutput,
    ScoreExplanationOutput,
)
from groundwork.models.schemas import (
    CompanyProfileFacts,
    CompanySeed,
    FundingEvent,
    IndustryProfileFact,
    PlaySpec,
    ResearchFacts,
)

_PLACEHOLDER_COMPANY = CompanySeed(
    slug="acme-robotics", name="Acme Robotics", domain="acme-robotics.com",
    industry="unknown", size_band="unknown", employee_count=0, hq_country="unknown",
)
from groundwork.providers.base import LLMOperation, LLMResult, ProviderBundle
from groundwork.repositories.plays import PlayRepository
from tests.search_live_helpers import make_search_provider, search_response, search_result


class ScriptedFakeLLM:
    """Deterministic per-operation outputs — this is a pipeline-wiring
    test, not another discovery/research correctness test, so the LLM
    behavior here is minimal and predictable rather than adversarial."""

    name = "fake_llm"

    async def structured(self, envelope, schema, *, ctx_key, operation):
        if operation == LLMOperation.DISCOVERY_EXTRACTION:
            refs = re.findall(r'ref="([^"]+)"', envelope.user)
            parsed = DiscoveryExtractionOutput(
                candidates=[DiscoveryCandidate(company_name="Acme Robotics", supporting_result_refs=refs[:1])]
            )
        elif operation == LLMOperation.RESEARCH_EXTRACTION:
            source_ref = re.search(r'ref="([^"]+)"', envelope.user)
            ref = source_ref.group(1) if source_ref else None
            facts = ResearchFacts(
                company=_PLACEHOLDER_COMPANY,
                funding_events=[FundingEvent(stage="series_b", claim="raised a Series B round", source_ref=ref)],
                profile=CompanyProfileFacts(industry=IndustryProfileFact(category="OTHER", claim="robotics company", source_ref=ref)),
            )
            parsed = ResearchExtractionOutput(facts=facts)
        elif operation == LLMOperation.SCORE_EXPLANATION:
            parsed = ScoreExplanationOutput(explanation="Real, live-researched signals support this score.")
        else:
            raise AssertionError(f"unexpected operation in this pipeline-wiring test: {operation}")
        return LLMResult(parsed=parsed, operation=operation, model="fake", provider="fake_llm", prompt_version="v1", attempts=[])


def _discovery_and_retrieval_steps() -> list:
    steps = [(200, search_response(results=[search_result(
        id="hit1", url="https://newswire.example.com/acme", title="Acme Robotics raises Series B",
        content="Acme Robotics today announced a new Series B funding round.",
    )])) for _ in range(4)]
    steps.append((200, search_response(results=[
        search_result(id="dom1", url="https://acme-robotics.com", title="Acme Robotics - Official Site")
    ])))
    # Per-company retrieval: 3 category queries, then 1 extract call.
    steps.append((200, search_response(results=[search_result(
        id="src1", url="https://acme-robotics.com/news/funding", title="Acme Robotics Series B",
        content="Acme Robotics closed a $20M Series B round.",
    )])))
    steps.append((200, search_response(results=[])))
    steps.append((200, search_response(results=[])))
    steps.append((200, {
        "results": [{"url": "https://acme-robotics.com/news/funding", "raw_content": "Acme Robotics closed a $20M Series B round led by Example Ventures."}],
        "failed_results": [],
    }))
    return steps


async def test_full_live_run_produces_real_prospect_with_live_fetch_evidence(session_factory) -> None:
    search, transport = make_search_provider(_discovery_and_retrieval_steps())
    providers = ProviderBundle(llm=ScriptedFakeLLM(), search=search)
    repos = Repos.build(session_factory)

    plays = PlayRepository(session_factory)
    play_id = await plays.create(name="t", objective_text="find robotics companies", icp_spec={}, mode="live")
    run_id = await repos.runs.create(play_id=play_id, mode="live", seed=1, provider_profile={})

    play_spec = PlaySpec(objective_text="find robotics companies", target_industries=["robotics"], target_count=1)
    summary = await execute_run(
        run_id=run_id, play_spec=play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=3, run_wall_clock_timeout_s=30.0, budget=DEMO_BUDGET,
        discovery_bounds=DiscoveryBounds(max_plan_queries=4, max_domain_resolution_queries=8),
    )

    assert len(summary.outcomes) == 1
    outcome = summary.outcomes[0]
    assert outcome.company.name == "Acme Robotics"
    assert outcome.company.domain == "acme-robotics.com"
    assert outcome.status.value in {"PASS", "NEEDS_REVIEW", "REJECTED"}  # reached a real review verdict

    evidence_rows = await repos.prospect_data.evidence_for_run(run_id)
    assert evidence_rows
    for row in evidence_rows:
        assert row.origin == EvidenceOrigin.LIVE_FETCH.value
        assert row.source_url and row.source_url.startswith("https://acme-robotics.com")

    company_row = await repos.companies.get(await _company_id_for_prospect(repos, outcome.prospect_id))
    assert company_row.origin == "live_fetch"

    evaluation = await compute_run_evaluation(run_id, repos)
    sq = evaluation["search_quality"]
    assert sq["result_occurrences"] >= 1
    assert sq["sources_used_as_evidence"] >= 1
    assert sq["domain_resolution_method_counts"] == {"deterministic": 1}


async def _company_id_for_prospect(repos: Repos, prospect_id: str) -> str:
    row = await repos.prospects.get(prospect_id)
    return row.company_id
