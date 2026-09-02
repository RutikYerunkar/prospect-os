"""In-process sliding-window rate limiter — Checkpoint I1 Phase 8/8B.

Deliberately process-local: correct for this project's actual deployment
shape (ONE API instance, ONE uvicorn worker — see docs/DEPLOYMENT.md), NOT a
distributed rate limit. Horizontal scaling would need a shared store (Redis,
a DB table) instead; that's explicitly out of scope for I1, and this module
makes no claim otherwise.

Used for: operator login failed-attempt limiting (Phase 8), unauthenticated
preview/write abuse limiting (Phase 8B).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Tracks timestamped "hits" per key over a trailing window. Two usage
    shapes, both supported: `allow(key)` (record-and-check every attempt in
    one call — for endpoints where every request, successful or not, should
    count) and the `is_blocked`/`record_failure` pair (for endpoints like
    operator login, where only FAILED attempts should count against the
    limit, so a legitimate operator who eventually gets the passphrase
    right isn't penalized for the attempts that got them there)."""

    def __init__(self, max_attempts: int, window_s: float) -> None:
        self._max_attempts = max_attempts
        self._window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and now - hits[0] > self._window_s:
            hits.popleft()
        return hits

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._prune(key, now)
        if len(hits) >= self._max_attempts:
            return False
        hits.append(now)
        return True

    def is_blocked(self, key: str) -> bool:
        now = time.monotonic()
        return len(self._prune(key, now)) >= self._max_attempts

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        self._prune(key, now)
        self._hits[key].append(now)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
