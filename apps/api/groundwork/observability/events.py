"""`EventEmitter` — pre-bound to `run_id` on `ProspectContext`. Every state
transition writes a `run_events` row first (§19); Checkpoint C replays these
over SSE. Event types per §19: run.started, plan.created, prospect.discovered,
prospect.stage_changed, step.started, step.completed, step.retrying,
step.failed, prospect.scored, prospect.reviewed, prospect.completed,
run.completed, run.failed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from groundwork.repositories.events import EventRepository


@dataclass
class EventEmitter:
    run_id: str
    events: EventRepository

    async def emit(self, event_type: str, *, prospect_id: str | None = None, **payload: Any) -> int:
        return await self.events.append(run_id=self.run_id, type=event_type, prospect_id=prospect_id, payload=payload)
