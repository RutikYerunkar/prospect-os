"""`SearchCallBudget` — per-run, lock-protected HARD ceilings on real Tavily
call volume (H2 Phase 3), the search-side analogue of `engine/run_budget.py::
RunBudget`. Unlike `RunBudget` (a soft, dollar-based, optional threshold),
these are always-enforced structural safety bounds — `LIVE_MAX_SEARCH_CALLS_
PER_RUN`/`LIVE_MAX_EXTRACT_CALLS_PER_RUN` from `config.py` — never optional,
never described as "soft."

Per-prospect bounds (`LIVE_MAX_SOURCE_QUERIES_PER_PROSPECT`, `LIVE_MAX_
RESULT_OCCURRENCES_PER_PROSPECT`, `LIVE_MAX_SOURCES_PER_PROSPECT`) and the
one-shot discovery bounds (`LIVE_MAX_PLAN_QUERIES_PER_RUN`, `LIVE_MAX_DOMAIN_
RESOLUTION_QUERIES_PER_RUN`) don't need a shared object — they're enforced
locally by truncating a query/result list at the call site, or (domain
resolution) inside `engine/discovery.py`'s own strictly-sequential loop.
Only the two bounds that are genuinely shared across CONCURRENT prospects
(fetch_sources's search + extract calls, all running under the same
per-prospect semaphore) need atomic check-and-increment — this is that
object, shared once per run, constructed in `providers/registry.py` exactly
like `RunBudget` is.

`reserve_*` is a single atomic check-and-increment (not gate-then-charge as
two steps) — two concurrent prospects racing the last remaining slot must
never both succeed.
"""

from __future__ import annotations

import asyncio


class SearchCallBudget:
    def __init__(self, *, max_search_calls: int, max_extract_calls: int) -> None:
        self._max_search_calls = max_search_calls
        self._max_extract_calls = max_extract_calls
        self._search_calls_used = 0
        self._extract_calls_used = 0
        self._lock = asyncio.Lock()

    async def reserve_search_call(self) -> bool:
        """Atomically claims one slot of the run's search-call budget.
        Returns False (and reserves nothing) once the cap is reached —
        callers must synthesize a NOT_ATTEMPTED_BUDGET telemetry row and
        skip the call entirely, never make it anyway."""
        async with self._lock:
            if self._search_calls_used >= self._max_search_calls:
                return False
            self._search_calls_used += 1
            return True

    async def reserve_extract_call(self) -> bool:
        async with self._lock:
            if self._extract_calls_used >= self._max_extract_calls:
                return False
            self._extract_calls_used += 1
            return True

    async def usage(self) -> dict[str, int]:
        async with self._lock:
            return {
                "search_calls_used": self._search_calls_used,
                "max_search_calls": self._max_search_calls,
                "extract_calls_used": self._extract_calls_used,
                "max_extract_calls": self._max_extract_calls,
            }
