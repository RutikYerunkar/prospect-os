"""`PipelineBudget` — the injectable set of per-step timeout/retry/backoff
constants `build_prospect_pipeline()` used to hardcode (Checkpoint G Phase 1).

`DEMO_BUDGET` reproduces every Checkpoint B–F constant exactly, byte-for-byte
— this is what keeps the canonical demo output unchanged. `live_budget()`
builds the equivalent object for Live Mode from `config.Settings`, entirely
outside `engine/` (the engine never reads `Settings` directly).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineBudget:
    default_step_timeout_s: float = 2.0
    research_timeout_s: float = 2.0
    research_max_retries: int = 2
    personalize_timeout_s: float = 2.0
    personalize_max_retries: int = 1
    backoffs_s: tuple[float, ...] = (0.4, 0.8, 1.6)
    max_concurrent_prospects: int = 3
    run_wall_clock_timeout_s: float = 180.0


# Reproduces the literal constants `engine/pipeline.py` and `engine/step.py`
# hardcoded through Checkpoint F. Do not change these defaults — that would
# silently change Demo Mode's behavior.
DEMO_BUDGET = PipelineBudget()
