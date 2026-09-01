"""`call_search()`/`call_discover()` — the single call sites the engine uses
to invoke `ctx.providers.search.fetch_sources(...)`/`providers.search.
discover(...)`, analogous to `engine/llm.py::call_structured()`. Own search
telemetry persistence (`search_calls`/`source_documents`) — providers
themselves never import a repository, exactly like the LLM boundary.

`call_discover()` exists because discovery is a real, active execution path
(`engine/runner.py::discover_and_dedupe()` calls it once per run, before any
prospect exists) and must not bypass the same persistence seam
`call_search()` already gives `fetch_sources()` — see H1's deviation-closure
pass. `resolve_domain()` has no engine-level call site yet (nothing in H1's
pipeline invokes it — that's H2 groundwork), so it deliberately has no
`call_resolve_domain()` wrapper here; adding one now would be inventing a
runtime caller H1 doesn't need, not closing a gap.
"""

from __future__ import annotations

from groundwork.engine.context import ProspectContext
from groundwork.models.schemas import PlaySpec, SourceDocument
from groundwork.observability.search_calls import SearchCallRecorder
from groundwork.providers.base import DiscoveryResult, ProviderBundle, SearchProviderError


async def call_search(ctx: ProspectContext) -> list[SourceDocument]:
    ctx_key = ctx.step_key("research")
    bundle = await ctx.providers.search.fetch_sources(ctx.company, ctx_key=ctx_key)
    await ctx.search_calls.record(telemetry=bundle.telemetry, documents=bundle.documents)
    return bundle.documents


async def call_discover(
    *, providers: ProviderBundle, play_spec: PlaySpec, limit: int, search_calls: SearchCallRecorder
) -> DiscoveryResult:
    """Run-level analogue of `call_search()` — no `ProspectContext` exists
    yet at discovery time, so this takes the pieces it needs directly
    rather than a ctx. `search_calls` here is bound to `(run_id,
    prospect_id=None)` — see `SearchCallRecorder`'s docstring.

    On failure, persists whatever telemetry the provider attached to the
    raised `SearchProviderError` (mirrors `engine/llm.py::call_structured`'s
    `except ProviderError` persist-then-reraise pattern exactly) before
    re-raising, so a failed discovery attempt is never silently lost from
    `search_calls` even though the run itself then fails.
    """
    try:
        result = await providers.search.discover(play_spec, limit)
    except SearchProviderError as exc:
        await search_calls.record(telemetry=exc.telemetry, documents=[])
        raise
    await search_calls.record(telemetry=result.telemetry, documents=[])
    return result
