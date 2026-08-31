"""GET /runs/{id}, the SSE event stream, and GET /runs/{id}/prospects.

The SSE generator's only source of truth is the `run_events` table (§19):
it replays every row with `seq > after_seq`, keeps polling for new rows
while the run is active, and closes once the run is terminal and no rows
remain unsent. Nothing is buffered in memory across requests — a second
connection with the same `after_seq` gets the identical replay.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from groundwork.api.deps import ApprovalsRepoDep, ReposDep
from groundwork.api.errors import NotFoundError
from groundwork.api.schemas import ProspectSummary, RunResponse
from groundwork.models.enums import RunStatus

router = APIRouter(prefix="/api/runs", tags=["runs"])

_TERMINAL_RUN_STATUSES = {RunStatus.COMPLETED.value, RunStatus.PARTIAL.value, RunStatus.INTERRUPTED.value}
_POLL_INTERVAL_S = 0.25
_HEARTBEAT_INTERVAL_S = 15.0


def _run_duration_ms(run_row) -> float | None:
    if run_row.started_at is None:
        return None
    end = run_row.finished_at or datetime.utcnow()
    return (end - run_row.started_at).total_seconds() * 1000


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, repos: ReposDep) -> RunResponse:
    row = await repos.runs.get(run_id)
    if row is None:
        raise NotFoundError(f"no run with id {run_id!r}")
    return RunResponse(
        id=row.id,
        play_id=row.play_id,
        status=row.status,
        mode=row.mode,
        seed=row.seed,
        plan=row.plan,
        counters=row.counters,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_ms=_run_duration_ms(row),
        error=row.error,
    )


def _format_sse(seq: int, event_type: str, data: dict[str, Any]) -> str:
    return f"id: {seq}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _event_stream(request: Request, run_id: str, after_seq: int, repos) -> AsyncGenerator[str, None]:
    last_seq = after_seq
    last_activity = time.monotonic()
    while True:
        if await request.is_disconnected():
            return

        events = await repos.events.after(run_id, last_seq)
        for event in events:
            last_seq = event.seq
            data = {
                "seq": event.seq,
                "run_id": event.run_id,
                "type": event.type,
                "ts": event.ts.isoformat(),
                "prospect_id": event.prospect_id,
                "payload": event.payload,
            }
            yield _format_sse(event.seq, event.type, data)
            last_activity = time.monotonic()

        if events:
            # More may already be waiting (independently-executing prospects
            # interleave their writes); check again immediately rather than
            # sleeping a full poll interval.
            continue

        run_row = await repos.runs.get(run_id)
        if run_row is None or run_row.status in _TERMINAL_RUN_STATUSES:
            return  # terminal and fully drained — close cleanly (§19)

        if time.monotonic() - last_activity >= _HEARTBEAT_INTERVAL_S:
            yield ": heartbeat\n\n"
            last_activity = time.monotonic()

        await asyncio.sleep(_POLL_INTERVAL_S)


@router.get("/{run_id}/events")
async def stream_run_events(
    request: Request, run_id: str, repos: ReposDep, after_seq: int = Query(default=0, ge=0)
) -> StreamingResponse:
    run_row = await repos.runs.get(run_id)
    if run_row is None:
        raise NotFoundError(f"no run with id {run_id!r}")
    return StreamingResponse(
        _event_stream(request, run_id, after_seq, repos),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/prospects", response_model=list[ProspectSummary])
async def list_run_prospects(run_id: str, repos: ReposDep, approvals: ApprovalsRepoDep) -> list[ProspectSummary]:
    run_row = await repos.runs.get(run_id)
    if run_row is None:
        raise NotFoundError(f"no run with id {run_id!r}")

    prospects = await repos.prospects.list_for_run(run_id)
    companies = await repos.companies.get_many([p.company_id for p in prospects])
    scores_by_prospect = {s.prospect_id: s for s in await repos.prospect_data.scores_for_run(run_id)}

    contacts_by_prospect: dict[str, Any] = {}
    for contact in await repos.prospect_data.contacts_for_run(run_id):
        contacts_by_prospect[contact.prospect_id] = contact

    signals_by_prospect: dict[str, list] = {}
    for signal in await repos.prospect_data.signals_for_run(run_id):
        signals_by_prospect.setdefault(signal.prospect_id, []).append(signal)

    retried_prospects = {t.prospect_id for t in await repos.tasks.for_run(run_id) if t.status == "RETRY"}
    approvals_by_prospect = await approvals.latest_for_prospects([p.id for p in prospects])

    summaries: list[ProspectSummary] = []
    for prospect in prospects:
        company = companies.get(prospect.company_id)
        score = scores_by_prospect.get(prospect.id)
        contact = contacts_by_prospect.get(prospect.id)
        top_signal = max(
            signals_by_prospect.get(prospect.id, []), key=lambda s: s.confidence, default=None
        )
        approval = approvals_by_prospect.get(prospect.id)

        summaries.append(
            ProspectSummary(
                id=prospect.id,
                run_id=run_id,
                company_name=company.display_name if company else "unknown",
                company_domain=company.canonical_domain if company else "",
                stage=prospect.current_stage,
                status=prospect.status,
                top_signal=top_signal.summary if top_signal else None,
                contact_verification=contact.verification if contact else None,
                contact_name=contact.full_name if contact else None,
                icp_score=score.overall if score else None,
                confidence=score.confidence if score else None,
                had_retry=prospect.id in retried_prospects,
                approval_state=approval.decision if approval else "PENDING",
                error=prospect.error,
            )
        )
    return summaries
