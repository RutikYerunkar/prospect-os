"""Fake-Live end-to-end: one prospect runs through the REAL pipeline
(`execute_run`), REAL rendered prompts (`prompts/*`), and a fake Responses
transport (`httpx2.MockTransport`) standing in for OpenAI — Checkpoint G
acceptance criterion #6. Search stays fixture-backed (LIVE LLM · FIXTURE
SEARCH); only the LLM provider is `OpenAILLMProvider`.
"""

from __future__ import annotations

import json

from groundwork.engine.budget import PipelineBudget
from groundwork.engine.runner import Repos, execute_run
from groundwork.providers.base import ProviderBundle
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import FixturePack, load_fixture_pack
from groundwork.providers.live.openai_llm import OpenAILLMProvider
from tests.live_helpers import make_runtime, message_output, response_body


def _one_company_pack(slug: str) -> FixturePack:
    pack = load_fixture_pack()
    company = pack.company_by_slug(slug)
    return FixturePack(play_spec=pack.play_spec, companies=[company])


def _research_body(company) -> dict:
    facts = {
        "company": {
            "slug": company.slug, "name": company.name, "domain": company.domain, "industry": company.industry,
            "size_band": company.size_band, "employee_count": company.employee_count,
            "hq_country": company.hq_country, "description": company.description,
        },
        "funding_events": [], "hiring_roles": [], "tech_mentions": [], "leadership": [],
    }
    return response_body(output=[message_output(json.dumps({"facts": facts}))])


def _score_body() -> dict:
    return response_body(output=[message_output(json.dumps({"explanation": "Live-mode explanation of the score."}))])


def _personalize_body() -> dict:
    return response_body(output=[message_output(json.dumps({"subject": "Hi", "body": "Hello there.", "claim_map": []}))])


async def test_fake_live_run_completes_end_to_end(session_factory):
    pack = _one_company_pack("sable-compute")
    company = pack.companies[0]

    # research (research LLM call), score (explanation), contact resolves
    # UNAVAILABLE with empty facts -> personalize is skipped -> only two
    # logical LLM calls actually fire for this prospect.
    steps = [(200, _research_body(company)), (200, _score_body())]
    runtime, transport = make_runtime(steps)
    llm_provider = OpenAILLMProvider(runtime=runtime)
    search_provider = DemoSearchProvider(pack, seed=42)
    providers = ProviderBundle(llm=llm_provider, search=search_provider)

    repos = Repos.build(session_factory)
    from groundwork.repositories.plays import PlayRepository

    plays = PlayRepository(session_factory)
    play_spec = pack.play_spec.model_copy(update={"target_count": 1})
    play_id = await plays.create(
        name="live test", objective_text=play_spec.objective_text,
        icp_spec=play_spec.model_dump(mode="json"), mode="live",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="live", seed=42)

    budget = PipelineBudget(
        default_step_timeout_s=10.0, research_timeout_s=10.0, research_max_retries=1,
        personalize_timeout_s=10.0, personalize_max_retries=0, backoffs_s=(0.1,),
        max_concurrent_prospects=1, run_wall_clock_timeout_s=60.0,
    )
    summary = await execute_run(
        run_id=run_id, play_spec=play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=1, run_wall_clock_timeout_s=60.0, budget=budget,
    )
    await runtime.close()

    assert len(summary.outcomes) == 1
    outcome = summary.outcomes[0]
    assert outcome.status.value in {"PASS", "NEEDS_REVIEW", "REJECTED"}
    assert outcome.score is not None

    llm_calls = await repos.llm_calls.for_run(run_id)
    assert len(llm_calls) == 2  # research + score; personalize skipped (no contact)
    assert {c.provider for c in llm_calls} == {"openai"}
    assert {c.operation for c in llm_calls} == {"research_extraction", "score_explanation"}
    assert all(c.status == "OK" for c in llm_calls)

    # Evidence stays fixture-backed/synthetic even in Live Mode — search
    # provider is unchanged (LIVE LLM · FIXTURE SEARCH).
    evidence = await repos.prospect_data.get_evidence(outcome.prospect_id)
    assert all(e.origin == "DEMO_FIXTURE" for e in evidence)
    assert all(e.source_url is None for e in evidence)
