"""`python -m groundwork.scripts.run_demo` — the Checkpoint B acceptance
criterion (§30/§31 of IMPLEMENTATION_PLAN.md).

Runs the complete Demo Mode engine headlessly — no FastAPI, no React — and
prints the execution trace plus the resulting status distribution. Nothing
here is hardcoded: six prospects are processed under bounded concurrency,
each in an isolated `ProspectContext`, and every score/status/duplicate/
review outcome below is computed by the real domain logic from the fixture
pack's evidence.
"""

from __future__ import annotations

import argparse
import asyncio

from groundwork.config import settings
from groundwork.db import SessionLocal, create_all, engine
from groundwork.engine.runner import Repos, RunSummary, execute_run
from groundwork.models.enums import Mode
from groundwork.providers.demo.fixtures import load_fixture_pack
from groundwork.providers.registry import build_provider_bundle
from groundwork.repositories.plays import PlayRepository
from groundwork.timeutil import utcnow


async def print_trace(repos: Repos, run_id: str) -> None:
    tasks = await repos.tasks.for_run(run_id)
    print("--- execution trace (one row per step attempt) ---")
    header = f"{'PROSPECT':<10} {'STEP':<12} {'ATTEMPT':<8} {'STATUS':<8} {'DURATION_MS':<12} DETAIL"
    print(header)
    for t in tasks:
        detail = (t.error_message or "")[:60]
        print(f"{t.prospect_id[:8]:<10} {t.step_name:<12} {t.attempt:<8} {t.status:<8} {t.duration_ms:<12.1f} {detail}")
    print()
    retries = [t for t in tasks if t.status == "RETRY"]
    print(f"retries recorded: {len(retries)}\n")


def print_outcomes(summary: RunSummary) -> None:
    print("--- prospect outcomes ---")
    for outcome in summary.outcomes:
        score_txt = f"score={outcome.score.overall}" if outcome.score else "score=n/a"
        review_txt = f"review={outcome.review.verdict.value}" if outcome.review else "review=n/a"
        print(f"{outcome.company.name:<28} {outcome.status.value:<14} {score_txt:<14} {review_txt}")
    print()

    print("--- status distribution (computed, not hardcoded) ---")
    for status, count in sorted(summary.counters.items()):
        print(f"  {status}: {count}")
    print(f"\nrun status: {summary.status}")


async def main(seed: int) -> None:
    await create_all()
    pack = load_fixture_pack()
    play_spec = pack.play_spec

    repos = Repos.build(SessionLocal)
    plays = PlayRepository(SessionLocal)

    # Replaces the old unconditional sweep_interrupted() — reap_stale()
    # with `stale_before=now` marks every currently-RUNNING row stale
    # (there's no other process that could legitimately still be advancing
    # one in this headless, single-shot script) INTERRUPTED, same net
    # effect for this use case (see repositories/runs.py::reap_stale).
    interrupted = await repos.runs.reap_stale(utcnow())
    if interrupted:
        print(f"marked {len(interrupted)} previously-RUNNING run(s) INTERRUPTED (honest crash recovery)")

    play_id = await plays.create(
        name="AI infrastructure GTM play",
        objective_text=play_spec.objective_text,
        icp_spec=play_spec.model_dump(mode="json"),
        mode=Mode.DEMO.value,
    )
    run_id = await repos.runs.create(play_id=play_id, mode=Mode.DEMO.value, seed=seed)
    providers = build_provider_bundle(Mode.DEMO, seed=seed, fixture_pack=pack)

    print(f"=== Groundwork headless demo run — run_id={run_id} seed={seed} ===")
    print(f"companies in fixture pack: {len(pack.companies)}, concurrency: {settings.max_concurrent_prospects}\n")

    summary = await execute_run(
        run_id=run_id,
        play_spec=play_spec,
        providers=providers,
        repos=repos,
        max_concurrent_prospects=settings.max_concurrent_prospects,
        run_wall_clock_timeout_s=settings.run_wall_clock_timeout_s,
    )

    await print_trace(repos, run_id)
    print_outcomes(summary)

    await engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Groundwork Demo Mode engine headlessly.")
    parser.add_argument("--seed", type=int, default=42, help="seed for reproducible jitter/latency")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args.seed))
