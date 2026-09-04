"""`provider_profile()` — the truthful, no-secrets snapshot of exactly what
executed a run (Checkpoint G Phase 7/8). Attached to `RunResponse` and
surfaced by `GET /settings/providers`, so a viewer can reconstruct: which
LLM provider/model/reasoning effort ran, which prompt versions, which search
provider (fixture-backed in both modes — Checkpoint G never does live web
search), the evidence origin that implies, the hard output/call/prospect
bounds, and the soft budget threshold plus whether it's even enforceable
(no pricing configured -> never).
"""

from __future__ import annotations

from typing import Any

from groundwork.domain.query_plan import QUERY_PLAN_VERSION
from groundwork.models.enums import Mode
from groundwork.prompts import (
    discovery_extraction,
    domain_selection,
    objective_parse,
    personalization,
    research_extraction,
    score_explanation,
)


def prompt_versions() -> dict[str, str]:
    return {
        "research_extraction": research_extraction.PROMPT_VERSION,
        "score_explanation": score_explanation.PROMPT_VERSION,
        "personalization": personalization.PROMPT_VERSION,
        "objective_parse": objective_parse.PROMPT_VERSION,
        "discovery_extraction": discovery_extraction.PROMPT_VERSION,
        "domain_selection": domain_selection.PROMPT_VERSION,
    }


def search_hard_bounds(settings) -> dict[str, int]:
    """The real search safety controls (H2 Phase 3/17) — every number here
    is an enforced structural cap, never advisory."""
    return {
        "live_max_plan_queries_per_run": settings.live_max_plan_queries_per_run,
        "live_max_domain_resolution_queries_per_run": settings.live_max_domain_resolution_queries_per_run,
        "live_max_source_queries_per_prospect": settings.live_max_source_queries_per_prospect,
        "live_max_search_calls_per_run": settings.live_max_search_calls_per_run,
        "search_max_transport_retries": settings.search_max_transport_retries,
        "live_max_result_occurrences_per_prospect": settings.live_max_result_occurrences_per_prospect,
        "live_max_sources_per_prospect": settings.live_max_sources_per_prospect,
        "live_max_extract_calls_per_run": settings.live_max_extract_calls_per_run,
        "live_max_search_results_per_query": settings.live_max_search_results_per_query,
        "search_max_concurrency": settings.search_max_concurrency,
        "live_max_source_excerpt_chars": settings.live_max_source_excerpt_chars,
    }


def build_provider_profile(mode: Mode, settings, run_budget=None) -> dict[str, Any]:
    if mode is Mode.DEMO:
        return {
            "mode": Mode.DEMO.value,
            "llm_provider": "demo_llm",
            "model": "demo-llm-v1",
            "reasoning_effort": None,
            "prompt_versions": {k: "demo-v1" for k in prompt_versions()},
            "search_provider": "demo_fixture",
            "synthetic_search": True,
            "evidence_origin": "DEMO_FIXTURE",
            "enrichment_provider": "demo_fixture",
            "enrichment_origin": "DEMO_FIXTURE",
            "llm_max_output_tokens": None,
            "llm_max_transport_retries": None,
            "llm_max_schema_retries": None,
            "live_max_prospects_per_run": None,
            "soft_budget_usd": None,
            "soft_budget_enforceable": False,
            "pricing_configured": False,
            "deterministic": True,
        }

    # H2: LIVE LLM · LIVE SEARCH — every NEW Live run requires BOTH a
    # configured OpenAI runtime AND a configured Tavily runtime before
    # `api/routers/plays.py::start_run` ever calls this, so any Mode.LIVE
    # profile built from here on is truthfully real-search. Historical
    # Checkpoint G rows already have their own `LIVE LLM · FIXTURE SEARCH`
    # provider_profile JSON persisted verbatim on their `runs` row — this
    # function is never called again for an existing run, so those records
    # are untouched and still render correctly.
    # V2-D: `enrichment_provider`/`enrichment_origin` are additive
    # provenance only — reading `settings.enrichment_provider` directly here
    # is safe because `api/routers/plays.py::start_run` already 422'd a
    # misconfigured `ENRICHMENT_PROVIDER=apollo` (missing key/runtime)
    # before this function is ever called, so "apollo" here always means
    # truly active for this run.
    enrichment_active = settings.enrichment_provider == "apollo"
    return {
        "mode": Mode.LIVE.value,
        "llm_provider": "openai",
        "model": settings.openai_model,
        "reasoning_effort": settings.openai_reasoning_effort or None,
        "prompt_versions": prompt_versions(),
        "search_provider": "tavily",
        "synthetic_search": False,
        "evidence_origin": "LIVE_FETCH",
        "enrichment_provider": "apollo" if enrichment_active else None,
        "enrichment_origin": "LIVE_PROVIDER" if enrichment_active else None,
        "query_plan_version": QUERY_PLAN_VERSION,
        "llm_max_output_tokens": settings.llm_max_output_tokens,
        "llm_max_transport_retries": settings.llm_max_transport_retries,
        "llm_max_schema_retries": settings.llm_max_schema_retries,
        "live_max_prospects_per_run": settings.live_max_prospects_per_run,
        "search_hard_bounds": search_hard_bounds(settings),
        "search_usage_capable": True,  # Tavily's include_usage response field, mapped when present
        "search_pricing_configured": settings.tavily_price_usd_per_credit is not None,
        "soft_budget_usd": run_budget.soft_limit_usd if run_budget is not None else settings.live_run_soft_budget_usd,
        "soft_budget_enforceable": (run_budget.enforceable if run_budget is not None else settings.live_run_soft_budget_usd is not None),
        "pricing_configured": (
            settings.openai_price_input_usd_per_mtok is not None and settings.openai_price_output_usd_per_mtok is not None
        ),
        "deterministic": False,
    }
