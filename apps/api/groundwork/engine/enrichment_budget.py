"""`EnrichmentCallBudget` — a per-run, lock-protected HARD ceiling on
enrichment-provider call volume (v2 §Part 4/§E), the enrichment-side
analogue of `engine/search_budget.py::SearchCallBudget`. Same idiom: an
atomic reserve-before-call check-and-increment (not gate-then-charge as two
steps), so two concurrent prospects racing the last remaining slot can never
both succeed.

Checked INSIDE the provider implementation, constructor-injected — exactly
where `SearchCallBudget`/`RunBudget` are checked (`TavilySearchProvider`/
`OpenAILLMProvider`), never at the `engine/enrichment.py::call_enrichment()`
call site itself. `None` (the default) means unbounded — never enforced,
never described as a cap.
"""

from __future__ import annotations

import asyncio


class EnrichmentCallBudget:
    def __init__(self, *, max_calls: int) -> None:
        self._max_calls = max_calls
        self._calls_used = 0
        self._lock = asyncio.Lock()

    async def reserve_call(self) -> bool:
        """Atomically claims one slot of the run's enrichment-call budget.
        Returns False (and reserves nothing) once the cap is reached —
        callers must synthesize a NOT_ATTEMPTED_BUDGET telemetry row and
        skip the call entirely, never make it anyway."""
        async with self._lock:
            if self._calls_used >= self._max_calls:
                return False
            self._calls_used += 1
            return True

    async def usage(self) -> dict[str, int]:
        async with self._lock:
            return {"calls_used": self._calls_used, "max_calls": self._max_calls}
