"""`call_enrichment()` — the single call site `engine/steps/contact_enrichment.py`
uses to invoke `ctx.providers.enrichment.enrich_person(...)`, analogous to
`engine/llm.py::call_structured()` / `engine/search.py::call_search()`.

CRITICAL BOUNDARY (mirrors the LLM/search boundaries exactly): `providers/`
never imports a repository — providers only *return* attempt telemetry (or
carry it on a raised `EnrichmentProviderError`). This module is the only
thing that persists it, via `ctx.enrichment_calls`
(`observability.enrichment_calls.EnrichmentCallRecorder`, pre-bound to
`(run_id, prospect_id)` exactly like `ctx.llm_calls`/`ctx.search_calls`).
"""

from __future__ import annotations

import logging
import uuid

from groundwork.engine.context import ProspectContext
from groundwork.providers.contact_base import (
    EnrichmentProviderError,
    PersonEnrichmentQuery,
    PersonEnrichmentResult,
)

logger = logging.getLogger(__name__)


def _total_latency_ms(telemetry: list) -> float:
    return sum(t.latency_ms for t in telemetry if t.latency_ms is not None)


async def call_enrichment(ctx: ProspectContext, query: PersonEnrichmentQuery) -> PersonEnrichmentResult | None:
    """`None` means no enrichment provider is wired for this run (Live Mode
    before V2-D, or enrichment disabled) — the caller treats that as
    NOT_ATTEMPTED, zero provider calls, never a fixture fallback."""
    provider = ctx.providers.enrichment
    if provider is None:
        return None

    ctx_key = ctx.step_key("contact_enrichment")
    call_group_id = str(uuid.uuid4())
    try:
        result = await provider.enrich_person(query, ctx_key=ctx_key)
    except EnrichmentProviderError as exc:
        # v2 §V2-F — the authoritative post-last-known-good states, even on
        # failure (e.g. `PROVIDER_ERROR`), flow onto `ctx.contact_channels`
        # so `domain/review.py::run_checks` sees the real state rather than
        # nothing at all. Retried by `Step` (ENRICHMENT_STEP_RETRYABLE); each
        # attempt simply overwrites this with its own latest result.
        ctx.contact_channels = await ctx.enrichment_calls.record_failure(
            call_group_id=call_group_id, telemetry=exc.telemetry
        )
        logger.warning(
            "enrichment call failed step=contact_enrichment attempts=%d",
            len(exc.telemetry),
            extra={
                "run_id": ctx.run_id, "prospect_id": ctx.prospect_id,
                "latency_ms": _total_latency_ms(exc.telemetry),
            },
        )
        raise

    ctx.contact_channels = await ctx.enrichment_calls.record_success(
        call_group_id=call_group_id,
        telemetry=result.telemetry,
        result=result,
        email_status_map=provider.email_status_map,
        grounded_full_name=ctx.contact.full_name if ctx.contact else None,
        grounded_company_name=ctx.company.name,
        grounded_company_domain=ctx.company.domain,
    )
    logger.info(
        "enrichment call ok step=contact_enrichment matched=%s attempts=%d",
        result.matched, len(result.telemetry),
        extra={
            "run_id": ctx.run_id, "prospect_id": ctx.prospect_id,
            "latency_ms": _total_latency_ms(result.telemetry),
        },
    )
    return result
