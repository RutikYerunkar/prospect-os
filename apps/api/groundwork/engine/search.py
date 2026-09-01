"""`call_search()` — the single call site pipeline steps use to invoke
`ctx.providers.search.fetch_sources(...)`, analogous to
`engine/llm.py::call_structured()`. Owns search telemetry persistence
(`search_calls`/`source_documents`) — providers themselves never import a
repository, exactly like the LLM boundary.
"""

from __future__ import annotations

from groundwork.engine.context import ProspectContext
from groundwork.models.schemas import SourceDocument


async def call_search(ctx: ProspectContext) -> list[SourceDocument]:
    ctx_key = ctx.step_key("research")
    bundle = await ctx.providers.search.fetch_sources(ctx.company, ctx_key=ctx_key)
    await ctx.search_calls.record(telemetry=bundle.telemetry, documents=bundle.documents)
    return bundle.documents
