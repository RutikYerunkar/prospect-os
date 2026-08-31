"""`python -m groundwork.scripts.live_smoke --i-understand-this-costs-money`

The OPTIONAL real-live smoke test (Checkpoint G). Exercises all FOUR Live
LLM operations — objective parse, research extraction, score explanation,
personalization — against the REAL OpenAI API, on exactly ONE fixture
prospect. This costs real money and makes real network calls — it must
NEVER run accidentally:

- Requires the exact `--i-understand-this-costs-money` flag.
- Requires `OPENAI_API_KEY` to actually be configured.
- Refuses to proceed (before making any paid call) if the local SQLite
  schema predates Checkpoint G — see `db.py::schema_upgrade_problems()`.
- Never runs as part of `make test`, CI, or any other automated path.

`--company` (default `sable-compute`) is deliberately NOT `northwind-labs`:
Northwind's fixture has a scripted research failure
(`fail_attempts=1, error=ProviderTimeout`) used to exercise Demo Mode's
retry path — reproducing that here would burn an extra real, billed
attempt for a scenario a real API essentially never hits. Sable Compute has
no scripted failure, so a smoke run's attempt count reflects genuine
provider behavior, not a fixture artifact. This is a deliberate choice, not
a promise that this company will PASS review — see docs/PROGRESS.md's
Checkpoint G section for why a real run can legitimately land REJECTED.

Prints the configured model/effort/bounds (and a dollar bound only if
trustworthy pricing is configured) BEFORE making any request, then prints
full per-attempt telemetry, actual token totals, cost (if configured),
score, outreach/claim map, and the full seven-check review result (not just
the verdict) AFTER. Fails loudly — nonzero exit — if ANY attempt was
TRUNCATED.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from groundwork.config import settings
from groundwork.db import SessionLocal, create_all, engine, schema_upgrade_problems
from groundwork.engine.budget import PipelineBudget
from groundwork.engine.objective_parser import parse_objective
from groundwork.engine.run_budget import RunBudget
from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import Mode
from groundwork.providers.base import ProviderBundle
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import FixturePack, load_fixture_pack
from groundwork.providers.live.openai_llm import OpenAILLMProvider
from groundwork.providers.live.runtime import LiveProviderRuntime
from groundwork.repositories.plays import PlayRepository

# Four operations: objective_parse, research_extraction, score_explanation,
# personalization (personalize may be skipped if no verified contact — see
# the smoke output's own note when that happens).
_OPERATIONS_COUNT = 4


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
        # Worst case across all four operations this smoke actually makes:
        # objective_parse, research_extraction, score_explanation, personalization.
        worst_tokens = _OPERATIONS_COUNT * max_attempts * settings.llm_max_output_tokens
        worst_cost = (worst_tokens / 1_000_000) * settings.openai_price_output_usd_per_mtok
        print(f"rough dollar bound (very conservative, output-only, {_OPERATIONS_COUNT} operations): ${worst_cost:.4f}")
    else:
        print("dollar bound: UNAVAILABLE — OPENAI_PRICE_*_USD_PER_MTOK not configured")
    print()


def _print_review(review) -> None:
    print(f"\nreview verdict: {review.verdict.value}")
    print("guardrail checks:")
    for check in review.checks:
        mark = "PASS" if check.passed else "FAIL"
        print(f"  [{mark}] {check.id:<20} ({check.severity:<4}) — {check.detail}")


async def main(one_company_slug: str) -> int:
    if not settings.openai_api_key:
        print("OPENAI_API_KEY is not configured — aborting.", file=sys.stderr)
        return 1

    _print_preamble()

    await create_all()
    problems = await schema_upgrade_problems(engine)
    if problems:
        print("Local Groundwork DB predates Checkpoint G:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "Run `make demo-reset` (or `python -m groundwork.scripts.reset`) once, then re-run this "
            "smoke test. Aborting BEFORE making any paid API call — your existing local data was not touched.",
            file=sys.stderr,
        )
        return 1

    pack = load_fixture_pack()
    company = pack.company_by_slug(one_company_slug)
    one_pack = FixturePack(play_spec=pack.play_spec, companies=[company])
    play_spec = pack.play_spec.model_copy(update={"target_count": 1})

    repos = Repos.build(SessionLocal)
    runtime = LiveProviderRuntime.create(settings)
    run_budget = RunBudget(settings.live_run_soft_budget_usd)
    llm_provider = OpenAILLMProvider(runtime=runtime, run_budget=run_budget)

    # Operation 1/4: objective parse — a real Live LLM call, same path
    # `POST /plays` takes with `use_live_objective_parser=True`. Falls back
    # deterministically on any provider failure (never aborts the smoke).
    # icp_overrides is deliberately the fixture's own canonical PlaySpec —
    # "user overrides always win" means whatever the model infers, the
    # pipeline still scores against the exact well-understood fixture
    # criteria, so this smoke's score/review output stays interpretable
    # regardless of what the live model happens to infer from the objective
    # text. The real inference still runs, is still billed, and its
    # attempts are still fully persisted/printed below — only the resulting
    # criteria used for the *pipeline* are pinned.
    parsed = await parse_objective(
        objective_text=play_spec.objective_text, icp_overrides=play_spec.model_dump(mode="json"),
        target_count=1, llm_provider=llm_provider, use_llm=True,
    )
    print(f"objective parse: parse_source={parsed.parse_source}, attempts={len(parsed.attempts)}")
    play_spec = parsed.play_spec

    play_kwargs = dict(
        name="live smoke test", objective_text=play_spec.objective_text,
        icp_spec=play_spec.model_dump(mode="json"), mode=Mode.LIVE.value,
    )
    if parsed.attempts:
        play_id = await repos.llm_calls.create_play_with_attempts(
            play_kwargs=play_kwargs, call_group_id=str(uuid.uuid4()), operation="objective_parse",
            provider=parsed.provider or "openai", prompt_version="objective_parse-v1", attempts=parsed.attempts,
        )
    else:
        play_id = await PlayRepository(SessionLocal).create(**play_kwargs)

    run_id = await repos.runs.create(play_id=play_id, mode=Mode.LIVE.value, seed=1)

    # Operations 2-4/4: research_extraction, score_explanation, personalization
    # — the real per-prospect pipeline, same LLM provider/budget instance so
    # RunBudget accounting covers all four operations together.
    providers = ProviderBundle(llm=llm_provider, search=DemoSearchProvider(one_pack, seed=1))
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

    pipeline_calls = await repos.llm_calls.for_run(run_id)
    play_calls = await repos.llm_calls.for_play(play_id)
    calls = play_calls + pipeline_calls
    print("\n--- per-attempt telemetry ---")
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
        if not outcome.drafts:
            print("outreach: skipped (no verified contact — see the 'contact' trace row)")
        for draft in outcome.drafts:
            print(f"outreach subject: {draft.subject}")
            print(f"claim_map entries: {len(draft.claim_map)}")
        if outcome.review:
            _print_review(outcome.review)

    await engine.dispose()

    if truncated:
        print("\nFAILURE: at least one attempt was TRUNCATED.", file=sys.stderr)
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--i-understand-this-costs-money", action="store_true", dest="confirmed",
        help="required — this makes real, billed OpenAI API calls",
    )
    parser.add_argument(
        "--company", default="sable-compute",
        help="fixture company slug to run (default: sable-compute — see module docstring for why)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.confirmed:
        print("Refusing to run without --i-understand-this-costs-money.", file=sys.stderr)
        sys.exit(1)
    sys.exit(asyncio.run(main(args.company)))
