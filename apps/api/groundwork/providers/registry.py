"""Mode -> provider bundle (§11). Demo Mode only in Checkpoint B; wiring an
`OpenAILLMProvider` / `TavilySearchProvider` in here is P1 work."""

from __future__ import annotations

from groundwork.models.enums import Mode
from groundwork.providers.base import ProviderBundle, ProviderNotConfigured
from groundwork.providers.demo.demo_llm import DemoLLMProvider
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import FixturePack, load_fixture_pack


def build_demo_provider_bundle(seed: int, fixture_pack: FixturePack | None = None) -> ProviderBundle:
    pack = fixture_pack or load_fixture_pack()
    return ProviderBundle(llm=DemoLLMProvider(pack, seed), search=DemoSearchProvider(pack, seed))


def build_provider_bundle(
    mode: Mode, seed: int, fixture_pack: FixturePack | None = None, *, live_runtime=None, run_budget=None
) -> ProviderBundle:
    """Mode -> `ProviderBundle`. Live Mode (Checkpoint G) is real OpenAI LLM +
    fixture-backed search — `LIVE LLM · FIXTURE SEARCH`, never live web
    search (that's Checkpoint H). Requesting Live without a configured
    `LiveProviderRuntime` raises `ProviderNotConfigured`, never a silent
    fallback to `DemoLLMProvider`.
    """
    if mode is Mode.DEMO:
        return build_demo_provider_bundle(seed, fixture_pack)

    if live_runtime is None:
        raise ProviderNotConfigured(
            "Live Mode requires a configured OPENAI_API_KEY and a running LiveProviderRuntime"
        )

    # Imported lazily so `providers/live/*` (which imports the `openai` SDK)
    # is never imported at all on the pure-Demo-Mode / no-credentials path —
    # a public clone with no OpenAI key must still run Demo Mode cleanly.
    from groundwork.providers.live.openai_llm import OpenAILLMProvider

    pack = fixture_pack or load_fixture_pack()
    return ProviderBundle(
        llm=OpenAILLMProvider(runtime=live_runtime, run_budget=run_budget),
        search=DemoSearchProvider(pack, seed),
    )
