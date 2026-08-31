from __future__ import annotations

from fastapi import APIRouter

from groundwork.api.deps import LiveRuntimeDep
from groundwork.api.schemas import LiveAvailability, ProviderInfo, ProviderSettingsResponse
from groundwork.config import settings
from groundwork.providers.profile import prompt_versions

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/providers", response_model=ProviderSettingsResponse)
async def get_provider_settings(live_runtime: LiveRuntimeDep) -> ProviderSettingsResponse:
    # Never returns key values (§21) — only whether a live-mode key is present.
    if settings.mode == "demo":
        llm = ProviderInfo(name="demo_llm", configured=True)
        search = ProviderInfo(name="demo_fixture", configured=True)
    else:
        llm = ProviderInfo(name="openai", configured=bool(settings.openai_api_key))
        search = ProviderInfo(name="tavily", configured=bool(settings.tavily_api_key))

    pricing_configured = (
        settings.openai_price_input_usd_per_mtok is not None and settings.openai_price_output_usd_per_mtok is not None
    )
    live = LiveAvailability(
        # Availability is the real runtime, never a silent Demo fallback —
        # `available` reflects whether Live Mode would actually work right now.
        available=live_runtime is not None and bool(settings.openai_api_key),
        model=settings.openai_model,
        reasoning_effort=settings.openai_reasoning_effort or None,
        prompt_versions=prompt_versions(),
        live_max_prospects_per_run=settings.live_max_prospects_per_run,
        llm_max_output_tokens=settings.llm_max_output_tokens,
        llm_max_transport_retries=settings.llm_max_transport_retries,
        llm_max_schema_retries=settings.llm_max_schema_retries,
        llm_call_deadline_s=settings.llm_call_deadline_s,
        live_step_timeout_s=settings.live_step_timeout_s,
        pricing_configured=pricing_configured,
        soft_budget_usd=settings.live_run_soft_budget_usd if pricing_configured else None,
        soft_budget_enforceable=pricing_configured and settings.live_run_soft_budget_usd is not None,
    )
    return ProviderSettingsResponse(mode=settings.mode, llm=llm, search=search, live=live)
