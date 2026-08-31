"""`python -m groundwork.scripts.live_smoke --i-understand-this-costs-money`

The OPTIONAL real-live smoke test (Checkpoint G). Runs exactly ONE prospect
through the REAL OpenAI API. This costs real money and makes real network
calls — it must NEVER run accidentally:

- Requires the exact `--i-understand-this-costs-money` flag.
- Requires `OPENAI_API_KEY` to actually be configured.
- Never runs as part of `make test`, CI, or any other automated path.

Prints the configured model/effort/bounds (and a dollar bound only if
trustworthy pricing is configured) BEFORE making the request, then prints
full per-attempt telemetry, actual token totals, cost (if configured),
score, outreach/claim map, and review verdict AFTER. Fails loudly — nonzero
exit — if ANY attempt was TRUNCATED.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from groundwork.config import settings
from groundwork.db import SessionLocal, create_all, engine
from groundwork.engine.budget import PipelineBudget
from groundwork.engine.run_budget import RunBudget
from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import Mode
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import FixturePack, load_fixture_pack
from groundwork.providers.live.openai_llm import OpenAILLMProvider
from groundwork.providers.live.runtime import LiveProviderRuntime
from groundwork.providers.base import ProviderBundle
from groundwork.repositories.plays import PlayRepository


def _print_preamble() -> None:
    print("=== Groundwork LIVE smoke test — REAL OpenAI API, REAL cost ===")
    print(f"model:              {settings.openai_model}")
    print(f"reasoning_effort:   {settings.openai_reasoning_effort or '(omitted)'}")
    print(f"llm_max_output_tokens: {settings.llm_max_output_tokens}")
    max_attempts = 1 + settings.llm_max_transport_retries + settings.llm_max_schema_retries
    print(f"hard attempt maximum per logical call: {max_attempts}")
    print(f"theoretical output-token maximum per logical call: {max_attempts * settings.llm_max_output_tokens}")
    pricing_ok = settings.openai_price_input_usd_per_mtok is not None and settings.openai_price_output_usd_per_mtok is not None
    if pricing_ok:
        # 3 logical calls (research, score, personalize) worst case.
        worst_tokens = 3 * max_attempts * settings.llm_max_output_tokens
        worst_cost = (worst_tokens / 1_000_000) * settings.openai_price_output_usd_per_mtok
        print(f"rough dollar bound (very conservative, output-only): ${worst_cost:.4f}")
    else:
        print("dollar bound: UNAVAILABLE — OPENAI_PRICE_*_USD_PER_MTOK not configured")
    print()


async def main(one_company_slug: str) -> int:
    if not settings.openai_api_key:
        print("OPENAI_API_KEY is not configured — aborting.", file=sys.stderr)
        return 1

    _print_preamble()

    await create_all()
    pack = load_fixture_pack()
    company = pack.company_by_slug(one_company_slug)
    one_pack = FixturePack(play_spec=pack.play_spec, companies=[company])
    play_spec = pack.play_spec.model_copy(update={"target_count": 1})

    repos = Repos.build(SessionLocal)
    plays = PlayRepository(SessionLocal)
    play_id = await plays.create(
        name="live smoke test", objective_text=play_spec.objective_text,
        icp_spec=play_spec.model_dump(mode="json"), mode=Mode.LIVE.value,
    )
    run_id = await repos.runs.create(play_id=play_id, mode=Mode.LIVE.value, seed=1)

    runtime = LiveProviderRuntime.create(settings)
    run_budget = RunBudget(settings.live_run_soft_budget_usd)
    providers = ProviderBundle(
        llm=OpenAILLMProvider(runtime=runtime, run_budget=run_budget),
        search=DemoSearchProvider(one_pack, seed=1),
    )
    budget = PipelineBudget(
        default_step_timeout_s=settings.live_step_timeout_s, research_timeout_s=settings.live_step_timeout_s,
        research_max_retries=2, personalize_timeout_s=settings.live_step_timeout_s, personalize_max_retries=1,
        backoffs_s=(0.4, 0.8, 1.6), max_concurrent_prospects=1,
        run_wall_clock_timeout_s=settings.live_run_wall_clock_timeout_s,
    )

    summary = await execute_run(
        run_id=run_id, play_spec=play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=1, run_wall_clock_timeout_s=budget.run_wall_clock_timeout_s, budget=budget,
    )
    await runtime.close()

    calls = await repos.llm_calls.for_run(run_id)
    print("--- per-attempt telemetry ---")
    truncated = False
    total_tokens_in = total_tokens_out = 0
    for c in calls:
        print(
            f"{c.operation:<22} attempt={c.attempt} kind={c.attempt_kind:<15} status={c.status:<10} "
            f"tokens_in={c.tokens_in} tokens_out={c.tokens_out} reasoning_tokens={c.reasoning_tokens} "
            f"cost_usd={c.cost_usd}"
        )
        total_tokens_in += c.tokens_in
        total_tokens_out += c.tokens_out
        if c.status == "TRUNCATED":
            truncated = True
    print(f"\ntotal tokens: in={total_tokens_in} out={total_tokens_out}")
    print(f"run soft budget spent: ${await run_budget.spent_usd():.6f}" if run_budget.enforceable else "run soft budget: not enforceable (no threshold configured)")

    if summary.outcomes:
        outcome = summary.outcomes[0]
        print(f"\nstatus: {outcome.status.value}")
        if outcome.score:
            print(f"score: {outcome.score.overall} ({outcome.score.explanation})")
        for draft in outcome.drafts:
            print(f"outreach subject: {draft.subject}")
            print(f"claim_map entries: {len(draft.claim_map)}")
        if outcome.review:
            print(f"review verdict: {outcome.review.verdict.value}")

    await engine.dispose()

    if truncated:
        print("\nFAILURE: at least one attempt was TRUNCATED.", file=sys.stderr)
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--i-understand-this-costs-money", action="store_true", dest="confirmed",
        help="required — this makes a real, billed OpenAI API call",
    )
    parser.add_argument("--company", default="sable-compute", help="fixture company slug to run (default: sable-compute)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.confirmed:
        print("Refusing to run without --i-understand-this-costs-money.", file=sys.stderr)
        sys.exit(1)
    sys.exit(asyncio.run(main(args.company)))
