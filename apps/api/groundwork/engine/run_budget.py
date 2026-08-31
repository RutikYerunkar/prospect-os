"""`RunBudget` — a per-run, lock-protected SOFT spending threshold
(Checkpoint G Phase 7). Explicitly not a hard ceiling: gate *before* a call
starts, charge *after* it completes; once tripped, future calls don't
start, but calls already in flight (bounded by `LLM_MAX_CONCURRENCY`) are
allowed to finish. `None` means no threshold is configured at all — never
enforced, never described as a cap.
"""

from __future__ import annotations

import asyncio


class RunBudget:
    def __init__(self, soft_limit_usd: float | None) -> None:
        self._soft_limit = soft_limit_usd
        self._spent_usd = 0.0
        self._lock = asyncio.Lock()

    @property
    def enforceable(self) -> bool:
        """False when no soft threshold is configured (no verified pricing,
        or the operator left it unset) — the UI must say so rather than
        imply a dollar figure is being watched."""
        return self._soft_limit is not None

    @property
    def soft_limit_usd(self) -> float | None:
        return self._soft_limit

    async def is_tripped(self) -> bool:
        async with self._lock:
            return self._soft_limit is not None and self._spent_usd >= self._soft_limit

    async def charge(self, cost_usd: float | None) -> None:
        if cost_usd is None:
            return
        async with self._lock:
            self._spent_usd += cost_usd

    async def spent_usd(self) -> float:
        async with self._lock:
            return self._spent_usd
