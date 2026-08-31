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

from groundwork.models.enums import Mode
from groundwork.prompts import objective_parse, personalization, research_extraction, score_explanation


def prompt_versions() -> dict[str, str]:
    return {
        "research_extraction": research_extraction.PROMPT_VERSION,
        "score_explanation": score_explanation.PROMPT_VERSION,
        "personalization": personalization.PROMPT_VERSION,
        "objective_parse": objective_parse.PROMPT_VERSION,
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
            "llm_max_output_tokens": None,
            "llm_max_transport_retries": None,
            "llm_max_schema_retries": None,
            "live_max_prospects_per_run": None,
            "soft_budget_usd": None,
            "soft_budget_enforceable": False,
            "pricing_configured": False,
            "deterministic": True,
        }

    return {
        "mode": Mode.LIVE.value,
        "llm_provider": "openai",
        "model": settings.openai_model,
        "reasoning_effort": settings.openai_reasoning_effort or None,
        "prompt_versions": prompt_versions(),
        # LIVE LLM · FIXTURE SEARCH — Checkpoint G's one true label. No live
        # web search exists yet (that's Checkpoint H); search stays the same
        # `DemoSearchProvider` reading `demo_pack.yaml`.
        "search_provider": "demo_fixture",
        "synthetic_search": True,
        "evidence_origin": "DEMO_FIXTURE",
        "llm_max_output_tokens": settings.llm_max_output_tokens,
        "llm_max_transport_retries": settings.llm_max_transport_retries,
        "llm_max_schema_retries": settings.llm_max_schema_retries,
        "live_max_prospects_per_run": settings.live_max_prospects_per_run,
        "soft_budget_usd": run_budget.soft_limit_usd if run_budget is not None else settings.live_run_soft_budget_usd,
        "soft_budget_enforceable": (run_budget.enforceable if run_budget is not None else settings.live_run_soft_budget_usd is not None),
        "pricing_configured": (
            settings.openai_price_input_usd_per_mtok is not None and settings.openai_price_output_usd_per_mtok is not None
        ),
        "deterministic": False,
    }
