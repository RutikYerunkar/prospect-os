from __future__ import annotations

from fastapi import APIRouter

from groundwork.api.schemas import ProviderInfo, ProviderSettingsResponse
from groundwork.config import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/providers", response_model=ProviderSettingsResponse)
async def get_provider_settings() -> ProviderSettingsResponse:
    # Never returns key values (§21) — only whether a live-mode key is present.
    if settings.mode == "demo":
        llm = ProviderInfo(name="demo_llm", configured=True)
        search = ProviderInfo(name="demo_fixture", configured=True)
    else:
        llm = ProviderInfo(name="openai", configured=bool(settings.openai_api_key))
        search = ProviderInfo(name="tavily", configured=bool(settings.tavily_api_key))
    return ProviderSettingsResponse(mode=settings.mode, llm=llm, search=search)
