from __future__ import annotations

from fastapi import APIRouter

from groundwork.api.deps import EnrichmentRuntimeDep, IsOperatorDep, LiveRuntimeDep, LiveSearchRuntimeDep
from groundwork.api.operator_auth import operator_login_configured
from groundwork.api.schemas import LiveAvailability, ProviderInfo, ProviderSettingsResponse
from groundwork.config import settings
from groundwork.domain.query_plan import QUERY_PLAN_VERSION
from groundwork.providers.profile import prompt_versions, search_hard_bounds

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/providers", response_model=ProviderSettingsResponse)
async def get_provider_settings(
    live_runtime: LiveRuntimeDep,
    search_runtime: LiveSearchRuntimeDep,
    enrichment_runtime: EnrichmentRuntimeDep,
    is_operator: IsOperatorDep,
) -> ProviderSettingsResponse:
    # Never returns key values (§21) — only whether a live-mode key is present.
    if settings.mode == "demo":
        llm = ProviderInfo(name="demo_llm", configured=True)
        search = ProviderInfo(name="demo_fixture", configured=True)
        enrichment = ProviderInfo(name="demo_fixture", configured=True)
    else:
        llm = ProviderInfo(name="openai", configured=bool(settings.openai_api_key))
        search = ProviderInfo(name="tavily", configured=bool(settings.tavily_api_key))
        # V2-D/V2-DH: "none" is a valid, fully-"configured" state (nothing is
        # needed for it) — only "apollo"/"hunter" care whether their own key
        # is present. Pinned by `test_hunter_activation.py`'s
        # `test_provider_info_configured_semantics_pinned` (§Part 14's
        # "audit and pin the existing behavior" note).
        if settings.enrichment_provider == "apollo":
            enrichment = ProviderInfo(name="apollo", configured=bool(settings.apollo_api_key))
        elif settings.enrichment_provider == "hunter":
            enrichment = ProviderInfo(name="hunter", configured=bool(settings.hunter_api_key))
        else:
            enrichment = ProviderInfo(name="none", configured=True)

    pricing_configured = (
        settings.openai_price_input_usd_per_mtok is not None and settings.openai_price_output_usd_per_mtok is not None
    )
    # H2: real availability requires BOTH runtimes — never a silent
    # fixture-search fallback when only OpenAI is configured.
    llm_available = live_runtime is not None and bool(settings.openai_api_key)
    search_available = search_runtime is not None and bool(settings.tavily_api_key)
    # V2-D/V2-DH: never part of `available`'s AND — enrichment is optional
    # even in Live Mode, so `enrichment_runtime` being unset
    # (ENRICHMENT_PROVIDER=none, by far the common case) must never disable
    # Live Mode itself.
    enrichment_available = enrichment_runtime is not None
    live = LiveAvailability(
        available=llm_available and search_available,
        llm_available=llm_available,
        search_available=search_available,
        enrichment_provider=settings.enrichment_provider,
        enrichment_available=enrichment_available,
        model=settings.openai_model,
        reasoning_effort=settings.openai_reasoning_effort or None,
        prompt_versions=prompt_versions(),
        search_provider="tavily",
        synthetic_search=False,
        query_plan_version=QUERY_PLAN_VERSION,
        live_max_prospects_per_run=settings.live_max_prospects_per_run,
        llm_max_output_tokens=settings.llm_max_output_tokens,
        llm_max_transport_retries=settings.llm_max_transport_retries,
        llm_max_schema_retries=settings.llm_max_schema_retries,
        llm_call_deadline_s=settings.llm_call_deadline_s,
        live_step_timeout_s=settings.live_step_timeout_s,
        search_hard_bounds=search_hard_bounds(settings),
        search_usage_capable=True,
        search_pricing_configured=settings.tavily_price_usd_per_credit is not None,
        pricing_configured=pricing_configured,
        soft_budget_usd=settings.live_run_soft_budget_usd if pricing_configured else None,
        soft_budget_enforceable=pricing_configured and settings.live_run_soft_budget_usd is not None,
        operator_login_configured=operator_login_configured(),
        is_operator=is_operator,
    )
    return ProviderSettingsResponse(
        mode=settings.mode, llm=llm, search=search, enrichment=enrichment, live=live,
        max_concurrent_prospects=settings.max_concurrent_prospects,
    )
