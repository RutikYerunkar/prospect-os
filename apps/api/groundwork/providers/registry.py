"""Mode -> provider bundle (§11). Demo Mode only in Checkpoint B; wiring an
`OpenAILLMProvider` / `TavilySearchProvider` in here is P1 work."""

from __future__ import annotations

import asyncio

from groundwork.models.enums import Mode
from groundwork.providers.base import ProviderBundle
from groundwork.providers.demo.demo_llm import DemoLLMProvider
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import FixturePack, load_fixture_pack


def build_provider_bundle(
    mode: Mode, seed: int, fixture_pack: FixturePack | None = None
) -> ProviderBundle:
    if mode is not Mode.DEMO:
        raise NotImplementedError("Live Mode providers are P1 — not implemented in Checkpoint B")

    pack = fixture_pack or load_fixture_pack()
    return ProviderBundle(
        llm=DemoLLMProvider(pack, seed),
        search=DemoSearchProvider(pack, seed),
        provider_semaphores={
            "llm": asyncio.Semaphore(5),
            "search": asyncio.Semaphore(5),
        },
    )
