"""Mode -> provider bundle (§11). Demo Mode only in Checkpoint B; wiring an
`OpenAILLMProvider` / `TavilySearchProvider` in here is P1 work."""

from __future__ import annotations

from groundwork.config import settings
from groundwork.engine.enrichment_budget import EnrichmentCallBudget
from groundwork.models.enums import Mode
from groundwork.providers.base import ProviderBundle, ProviderNotConfigured
from groundwork.providers.demo.contact_enrichment import DemoEnrichmentProvider
from groundwork.providers.demo.demo_llm import DemoLLMProvider
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import FixturePack, load_fixture_pack


def build_demo_provider_bundle(seed: int, fixture_pack: FixturePack | None = None) -> ProviderBundle:
    pack = fixture_pack or load_fixture_pack()
    enrichment_budget = EnrichmentCallBudget(max_calls=settings.max_enrichment_calls_per_run)
    return ProviderBundle(
        llm=DemoLLMProvider(pack, seed),
        search=DemoSearchProvider(pack, seed),
        enrichment=DemoEnrichmentProvider(pack, seed, budget=enrichment_budget),
    )


def build_provider_bundle(
    mode: Mode,
    seed: int,
    fixture_pack: FixturePack | None = None,
    *,
    live_runtime=None,
    run_budget=None,
    search_runtime=None,
    search_budget=None,
    search_bounds: dict | None = None,
    enrichment_runtime=None,
    enrichment_budget=None,
) -> ProviderBundle:
    """Mode -> `ProviderBundle`.

    H2: Live Mode is real OpenAI LLM + real Tavily search — `LIVE LLM ·
    LIVE SEARCH`. Requesting Live without BOTH a configured
    `LiveProviderRuntime` (OpenAI) AND a configured `LiveSearchRuntime`
    (Tavily) raises `ProviderNotConfigured`, never a silent fallback to
    `DemoLLMProvider`/`DemoSearchProvider` for either half — see H1/H2's
    "no Live -> fixture fallback" invariant.

    V2-D/V2-DH: enrichment is DIFFERENT from LLM/search — it's optional even
    in Live Mode. `enrichment_runtime is None` (whether because
    `ENRICHMENT_PROVIDER=none`, or because the caller already 422'd a
    misconfigured `ENRICHMENT_PROVIDER=apollo|hunter` before ever reaching
    this function) simply means `enrichment=None` on the bundle ->
    NOT_ATTEMPTED, never an error and never a fixture fallback. The caller
    (`api/routers/plays.py::start_run`) alone decides whether a missing
    runtime should have blocked the run — this function only wires whatever
    it's handed. Which concrete provider gets wired (Apollo vs Hunter, never
    both) is read from `settings.enrichment_provider` — `"hunter"` selects
    `HunterEnrichmentProvider`; anything else (including a non-"none" value
    a direct caller hands in without setting it) preserves V2-D's original
    Apollo default.
    """
    if mode is Mode.DEMO:
        return build_demo_provider_bundle(seed, fixture_pack)

    if live_runtime is None:
        raise ProviderNotConfigured(
            "Live Mode requires a configured OPENAI_API_KEY and a running LiveProviderRuntime"
        )
    if search_runtime is None:
        raise ProviderNotConfigured(
            "Live Mode requires a configured TAVILY_API_KEY and a running LiveSearchRuntime"
        )

    # Imported lazily so `providers/live/*` (which imports the `openai`/
    # `tavily` SDKs) is never imported at all on the pure-Demo-Mode /
    # no-credentials path — a public clone with no keys must still run Demo
    # Mode cleanly.
    from groundwork.providers.live.openai_llm import OpenAILLMProvider
    from groundwork.providers.live.tavily_search import TavilySearchProvider

    enrichment = None
    if enrichment_runtime is not None:
        # Imported lazily for the same reason as the two providers above —
        # a run with `ENRICHMENT_PROVIDER=none` (the default) must never
        # import either module, mirroring the "stray key activates nothing"
        # invariant. Exactly one of Apollo/Hunter is ever wired.
        if settings.enrichment_provider == "hunter":
            from groundwork.providers.live.hunter_enrichment import HunterEnrichmentProvider

            enrichment = HunterEnrichmentProvider(runtime=enrichment_runtime, budget=enrichment_budget)
        else:
            from groundwork.providers.live.apollo_enrichment import ApolloEnrichmentProvider

            enrichment = ApolloEnrichmentProvider(runtime=enrichment_runtime, budget=enrichment_budget)

    bounds = search_bounds or {}
    return ProviderBundle(
        llm=OpenAILLMProvider(runtime=live_runtime, run_budget=run_budget),
        search=TavilySearchProvider(
            runtime=search_runtime,
            search_budget=search_budget,
            max_results_per_query=bounds.get("max_results_per_query", 10),
            max_source_queries_per_prospect=bounds.get("max_source_queries_per_prospect", 3),
            max_result_occurrences_per_prospect=bounds.get("max_result_occurrences_per_prospect", 15),
            max_sources_per_prospect=bounds.get("max_sources_per_prospect", 5),
            max_source_excerpt_chars=bounds.get("max_source_excerpt_chars", 1200),
        ),
        enrichment=enrichment,
    )
