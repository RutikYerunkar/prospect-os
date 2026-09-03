"""`Pipeline` — a topologically ordered list of `Step`s, not a generic DAG
engine. `engine/` is capped at ~400 LOC by design (§30/§33): this is a
handful of lines, not a framework."""

from __future__ import annotations

from dataclasses import dataclass

from groundwork.engine.budget import DEMO_BUDGET, PipelineBudget
from groundwork.engine.context import ProspectContext
from groundwork.engine.step import Step, StepResult
from groundwork.engine.steps.contact import contact
from groundwork.engine.steps.contact_enrichment import contact_enrichment
from groundwork.engine.steps.enrich import enrich
from groundwork.engine.steps.personalize import personalize
from groundwork.engine.steps.research import research
from groundwork.engine.steps.review import review
from groundwork.engine.steps.score import score
from groundwork.engine.steps.signals import signals
from groundwork.models.enums import ProspectStage
from groundwork.providers.base import STEP_RETRYABLE
from groundwork.providers.contact_base import ENRICHMENT_STEP_RETRYABLE

STAGE_BY_STEP: dict[str, ProspectStage] = {
    "research": ProspectStage.RESEARCH,
    "signals": ProspectStage.SIGNALS,
    "enrich": ProspectStage.ENRICH,
    "score": ProspectStage.SCORE,
    "contact": ProspectStage.CONTACT,
    # v2: `contact_enrichment` deliberately has no `STAGE_BY_STEP` entry — it
    # is an additive sub-step of contact resolution (same CONTACT phase, no
    # new `ProspectStage` member minted for it), so it emits `step.started`/
    # `step.completed` like every step but no extra `prospect.stage_changed`.
    "personalize": ProspectStage.PERSONALIZE,
    "review": ProspectStage.REVIEW,
}


def topological_order(steps: list[Step]) -> list[Step]:
    """Kahn's algorithm over `depends_on`. Raises on a cycle — a bug, not a
    runtime condition this pipeline is designed to handle gracefully."""
    by_name = {s.name: s for s in steps}
    in_degree = {s.name: 0 for s in steps}
    for s in steps:
        for dep in s.depends_on:
            in_degree[s.name] += 1

    ready = [s for s in steps if in_degree[s.name] == 0]
    ordered: list[Step] = []
    while ready:
        step = ready.pop(0)
        ordered.append(step)
        for other in steps:
            if step.name in other.depends_on:
                in_degree[other.name] -= 1
                if in_degree[other.name] == 0:
                    ready.append(by_name[other.name])

    if len(ordered) != len(steps):
        raise ValueError("cycle detected in pipeline step dependencies")
    return ordered


@dataclass
class Pipeline:
    steps: list[Step]

    def __post_init__(self) -> None:
        self.steps = topological_order(self.steps)

    async def execute(self, ctx: ProspectContext) -> None:
        for step in self.steps:
            stage = STAGE_BY_STEP.get(step.name)
            if stage is not None:
                ctx.stage = stage
                await ctx.events.emit("prospect.stage_changed", prospect_id=ctx.prospect_id, stage=stage.value)

            await ctx.events.emit("step.started", prospect_id=ctx.prospect_id, step=step.name)
            result: StepResult = await step.execute(ctx)
            await ctx.events.emit(
                "step.completed", prospect_id=ctx.prospect_id, step=step.name,
                ok=result.ok, skipped=result.skipped, detail=result.detail,
            )


def build_prospect_pipeline(budget: PipelineBudget = DEMO_BUDGET) -> Pipeline:
    """Research -> Signals -> Enrich -> Score -> Contact -> Personalize ->
    Review (§2). Research is the only step with real retries in the base
    fixtures — Contact and Personalize are `optional` so a degraded buyer
    lookup or a failed draft never sinks the whole prospect.

    Every timeout/retry/backoff constant comes from `budget` — never
    hardcoded here — so `execute_run(budget=...)` can hand Live Mode its own
    `PipelineBudget` (built from `Settings` outside `engine/`) while
    `DEMO_BUDGET`'s defaults reproduce Checkpoint B–F's literals exactly.
    """
    d = budget.default_step_timeout_s
    return Pipeline(
        steps=[
            Step(
                name="research", run_fn=research, timeout_s=budget.research_timeout_s,
                max_retries=budget.research_max_retries, retry_on=STEP_RETRYABLE, backoffs_s=budget.backoffs_s,
            ),
            Step(name="signals", run_fn=signals, depends_on=("research",), timeout_s=d),
            Step(name="enrich", run_fn=enrich, depends_on=("signals",), timeout_s=d),
            Step(name="score", run_fn=score, depends_on=("enrich",), timeout_s=d),
            Step(name="contact", run_fn=contact, depends_on=("score",), timeout_s=d, optional=True),
            # v2 — never named "enrich" (C4: that name is already taken by
            # the v1 field-precedence merge step above). Optional: a
            # contact-enrichment provider failure degrades this one
            # prospect's enrichment rather than crashing it (§Part 4/§F).
            Step(
                name="contact_enrichment", run_fn=contact_enrichment, depends_on=("contact",), timeout_s=d,
                max_retries=budget.contact_enrichment_max_retries, retry_on=ENRICHMENT_STEP_RETRYABLE,
                optional=True, backoffs_s=budget.backoffs_s,
            ),
            Step(
                name="personalize", run_fn=personalize, depends_on=("contact_enrichment",),
                timeout_s=budget.personalize_timeout_s,
                max_retries=budget.personalize_max_retries, retry_on=STEP_RETRYABLE, optional=True,
                backoffs_s=budget.backoffs_s,
            ),
            Step(name="review", run_fn=review, depends_on=("personalize",), timeout_s=d),
        ]
    )
