# Groundwork — Progress

Living state. Read this before touching anything — it tells you what's done, what's next, and what
not to re-litigate. Updated and committed at every checkpoint boundary (see
`docs/IMPLEMENTATION_PLAN.md` §30 for the checkpoint protocol).

---

## Completed checkpoints

| Checkpoint | Commit | Summary |
|---|---|---|
| **A — Foundation** | `6fafaa2414b2f3b75f8d0e9f2c36fe4003da9d09` (merged to `master` via PR #1) | Repo scaffolding, project-memory docs, FastAPI + Next.js health-check loop, CORS. |
| **B — Core engine** | `5edff10` (merged to `master` via PR #2) | Domain layer, fixtures, engine, demo providers, tracing/events, tests, headless demo. |
| **C — API / SSE** | *this commit* (branch `claude/checkpoint-c-api-sse-1fki7i`) | FastAPI routers for every P0 endpoint, async run launch (202), resumable SSE over `run_events`, computed-on-read evaluation metrics, approve/reject as state transitions, tests. |

---

## Current checkpoint

**C — API / SSE.** Status: **complete**, ready to stop per the checkpoint protocol.

Next session should start **Checkpoint D — Hero product UI** (see `docs/IMPLEMENTATION_PLAN.md` §30),
after reading `CLAUDE.md`, `docs/ARCHITECTURE.md`, and this file. **Do not build any React until
this session's SSE curl verification below has been read** — it already is; the API is proven from
the outside without a UI.

---

## What was built

**Models** (`apps/api/groundwork/models/`)
- `enums.py` — `Mode`, `EvidenceOrigin`, `SignalType`, `ContactVerification`, `ProspectStage`,
  `ProspectStatus`, `ReviewVerdict`, `RunStatus`, `StepStatus`.
- `schemas.py` — every domain Pydantic model (`PlaySpec`, `CompanySeed`, `Evidence`, `ResearchFacts`
  and its fact types, `Signal`, `ICPScore`/`DimensionScore`/`ScoreModifier`, `Contact`,
  `OutreachDraft`/`ClaimMapEntry`, `ReviewCheck`/`ReviewResult`, `ProspectOutcome`). `Evidence` carries
  the §12 provenance `model_validator`: only `LIVE_FETCH` may carry an `http(s)` `source_url`; every
  other origin is rejected if it tries to.
- `llm_io.py` — `ResearchExtractionOutput`, `PersonalizationOutput`, `ScoreExplanationOutput` (the
  three structured-output shapes the demo LLM actually returns).
- `tables.py` — the full SQLAlchemy schema from §20 (`plays`, `runs`, `companies`, `prospects`,
  `evidence`, `signals`, `icp_scores`, `contacts`, `outreach_drafts`, `review_results`, `approvals`,
  `agent_tasks`, `run_events`). `create_all()` only, no Alembic (as planned for this stage).

**Domain** (`apps/api/groundwork/domain/` — pure, zero I/O, verified by `grep` to import only from
`models/`)
- `dedupe.py` — domain/name normalization, dedupe key (domain-first, name fallback).
- `grounding.py` — token-overlap claim verification against an evidence snippet.
- `scoring.py` — the eight-dimension weighted ICP rubric exactly per §13, including the evidence gate,
  the hard disqualifier modifier, and `confidence = supported/total`. `industry_fit`/`size_fit` are
  treated as always-supported structural facts (from `CompanySeed`, not a claim needing evidence); the
  other six dimensions are gated on `evidence_ids` being non-empty.
- `review.py` — all seven deterministic checks (`claim_grounding`, `no_fabricated_contact`,
  `cross_prospect_leak`, `no_placeholders`, `duplicate_account`, `score_support`,
  `confidence_floor`) and the verdict rule (any hard fail → `FAIL`; else any soft fail →
  `NEEDS_REVIEW`; else `PASS`).

**Providers** (`apps/api/groundwork/providers/`)
- `base.py` — `LLMProvider`/`SearchProvider` Protocols, `ProviderBundle`, `PromptEnvelope`,
  `ProviderTimeout`/`ProviderUnavailable`/`SchemaViolation`, and `make_ctx_key`/`parse_ctx_key`/
  `stable_seed`. **Deviation from the plan's illustrative snippet:** seeded jitter uses a sha256-based
  `stable_seed()` instead of Python's builtin `hash()`, because `hash()` on strings is
  process-salted and would silently break the "a run is replayable by seed" claim.
  **Minor signature deviation:** `SearchProvider.fetch_sources` takes an extra `ctx_key` keyword
  (needed to scope scripted-failure attempt counting and jitter to one `(run_id, prospect_id,
  step)`), since the plan's §11 snippet was explicitly "shape, not final code."
- `demo/fixtures.py` — typed loader for the YAML fixture pack.
- `demo/demo_search.py`, `demo/demo_llm.py` — Demo Mode implementations. Fixture-derived structured
  output for research extraction; deterministic templating (from `ctx`-only envelope metadata, never
  the fixture pack) for personalization and the score explanation. Seeded jitter and
  fixture-configured scripted retry/failure are both generic over any step name a fixture names, not
  hardcoded to "research".
- `registry.py` — `build_provider_bundle(mode, seed, fixture_pack)`; raises `NotImplementedError` for
  `Mode.LIVE` (P1, not built).

**Engine** (`apps/api/groundwork/engine/` — core machinery ~484 LOC incl. docstrings; the ~400 LOC
guidance in §33 was aimed at preventing a generic DAG/plugin framework, which this isn't — it's a
fixed 7-step list, a retry/timeout wrapper, and a semaphore-bounded fan-out)
- `context.py` — `ProspectContext`, the isolation boundary. Every mutable field lives here; nothing
  else accumulates cross-prospect state. Carries read-only `other_dedupe_keys`/
  `other_company_identifiers` (names/domains only, never evidence/facts/scores) so the
  `cross_prospect_leak` and `duplicate_account` checks have something to check without breaking
  isolation.
- `step.py` — `Step`/`StepResult`. One `agent_tasks` row per attempt, exponential backoff + jitter
  (0.4s/0.8s/1.6s per §10), idempotency via `TraceRecorder.has_succeeded`, `optional` steps degrade
  instead of failing the prospect.
- `pipeline.py` — `Pipeline` with a real Kahn's-algorithm `topological_order()` over `depends_on` (not
  just an assumed-correct fixed list), plus `build_prospect_pipeline()` wiring Research → Signals →
  Enrich → Score → Contact → Personalize → Review. Research has `max_retries=2`; Contact and
  Personalize are `optional=True`.
- `runner.py` — `discover_and_dedupe()` (sequential, cheap) + `execute_run()`: global
  `asyncio.Semaphore(max_concurrent_prospects)`, one coroutine per prospect,
  `asyncio.gather(*tasks, return_exceptions=True)`, a run-level wall-clock watchdog
  (`asyncio.wait_for` around the gather) that marks any still-running prospect `TIMED_OUT` on
  expiry. Final prospect status is derived as: `DUPLICATE` (pre-pipeline) → `FAILED` (unhandled
  exception) → `REJECTED` (score disqualified, or review verdict `FAIL`) → `NEEDS_REVIEW` (review
  verdict `NEEDS_REVIEW`) → `PASS`.
- `steps/` — `research.py` (fetch sources → `Evidence` + LLM-extracted `ResearchFacts`, naive
  `source_ref → evidence_id` linking only), `signals.py` (the deterministic verifier half of the §9
  Hybrid design — demotes any fact whose claim doesn't token-overlap its cited evidence's snippet by
  clearing its `evidence_ids`, and always records a `Signal` either way), `enrich.py` (resolves a
  preliminary `Contact` from grounded leadership facts — needed before Score, since
  `persona_availability` is one of its dimensions, even though Contact is later in the pipeline order),
  `score.py` (calls `domain/scoring.py`, then one LLM call that writes only `explanation` from the
  finished numbers), `contact.py` (optional finalize/fallback), `personalize.py` (skipped, never
  fabricated, when `contact.verification == UNAVAILABLE`; envelope built only from this prospect's own
  grounded signals), `review.py` (calls `domain/review.py`).

**Observability** (`apps/api/groundwork/observability/`)
- `trace.py` — `TraceRecorder`, pre-bound to `(run_id, prospect_id)`, wraps `TaskRepository`.
- `events.py` — `EventEmitter`, pre-bound to `run_id`, wraps `EventRepository`. Event types emitted:
  `prospect.discovered`, `prospect.stage_changed`, `step.started`, `step.completed`, `step.retrying`,
  `prospect.scored`, `prospect.reviewed`, `prospect.completed`. (`run.started`/`run.completed`/
  `plan.created` are not yet emitted — natural Checkpoint C additions once the API wires a `Run`
  lifecycle around `execute_run`.)

**Repositories** (`apps/api/groundwork/repositories/`) — `plays.py`, `runs.py` (+ `sweep_interrupted()`
for the honest-crash-recovery sweep), `prospects.py` (`CompanyRepository` + `ProspectRepository`),
`prospect_data.py` (evidence/signals/scores/contacts/drafts/review results), `tasks.py` (`agent_tasks`
+ idempotency check), `events.py` (append-only `run_events`, `after_seq` query ready for Checkpoint
C's SSE replay).

**Fixtures** (`apps/api/groundwork/fixtures/demo_pack.yaml`) — 7 companies (6 required + the optional
Sable Compute, added since the six landed comfortably and all tests were green): Northwind Labs
(`PASS`, transient retry), Riverbend Analytics (`NEEDS_REVIEW`, ungrounded funding claim demoted),
Northwind Labs Inc. (`DUPLICATE`), Cobalt Retail Systems (`REJECTED`, hard disqualifier), Ferrous Grid
(`NEEDS_REVIEW`, contact `UNAVAILABLE` → personalization skipped), Quarry Systems (`FAILED`, retries
exhausted), Sable Compute (`PASS`). Every company has evidence only — no precomputed score or status
field anywhere in the YAML.

**Scripts** (`apps/api/groundwork/scripts/`) — `seed.py` (schema + fixture validation), `reset.py`
(wipe SQLite file incl. `-wal`/`-shm`, recreate schema), `run_demo.py` (the acceptance script: prints
the full execution trace and the computed status distribution). `Makefile`'s `seed`/`demo-reset` stubs
are now wired to these; added a `make demo` target for `run_demo.py`.

---

## What Checkpoint C added

**Repository read methods** (extended, not restructured — every Checkpoint B write path is
untouched): `PlayRepository.get/list`, `RunRepository.for_play`, `CompanyRepository.get/get_many`,
`ProspectRepository.get/list_for_run`, `ProspectDataRepository` gained single-prospect getters
(`get_evidence/get_signals/get_score/get_contact/get_drafts/get_review`) plus batched
`*_for_run` variants for the evaluation endpoint, `TaskRepository.for_prospect`. New
`repositories/approvals.py` (`ApprovalRepository`) — the `approvals` audit table's read/write path;
did not exist in Checkpoint B.

**`groundwork/evaluation/metrics.py`** — `compute_run_evaluation(run_id, repos)`, computed on read
from `runs`/`prospects`/`evidence`/`icp_scores`/`contacts`/`outreach_drafts`/`review_results`/
`agent_tasks` rows for one run. No `evaluation_metrics` table, nothing hardcoded: a metric that can't
be computed yet (e.g. no scores recorded) is `null`. Grounded-claim-rate reuses
`domain/grounding.verify_claim_evidence` directly against reconstructed `Evidence` models rather than
re-implementing the check — the same function the engine's own `claim_grounding` review check calls.

**`groundwork/api/`** (new package, isolated from `domain/`/`engine/` — routers call repositories and
`evaluation/metrics.py`, never the other way around):
- `deps.py` — `get_session_factory()` is the one seam tests override
  (`app.dependency_overrides[get_session_factory]`); `get_repos`/`get_plays_repo`/`get_approvals_repo`
  all depend on it via FastAPI `Depends`, so one override retargets an entire request to an isolated
  per-test SQLite file.
- `errors.py` — three exception types (`NotFoundError` 404, `ConflictError` 409,
  `UnprocessableEntityError` 422) rendered as RFC-7807 `{type, title, detail, status}` JSON by one
  registered handler. Pydantic's own request-body validation errors are left as FastAPI's default 422
  shape, per §21's own note — not reimplemented.
- `schemas.py` — the HTTP-facing request/response DTOs, kept separate from
  `groundwork/models/schemas.py` (the engine's own domain models never gain API/web awareness).
- `run_service.py` — `launch_run()` fires `execute_run` via `asyncio.create_task` and holds the task
  in a module-level set purely so it isn't garbage-collected mid-flight (not a `RunRegistry` for
  querying status — status is always read straight from the `runs` table). Emits `run.started` before
  and `run.completed`/`run.failed` after `execute_run` — the three run-level event types §19 lists
  that Checkpoint B's engine doesn't emit itself (per-prospect events were already there). If
  `execute_run` itself raises before/around the per-prospect fan-out, the run is finalized `PARTIAL`
  with the error recorded rather than left `RUNNING` forever — the run-level equivalent of the
  engine's own per-prospect failure isolation.
- `routers/plays.py` — `POST/GET /plays`, `GET /plays/{id}`, `POST /plays/{id}/runs`. No Objective
  Parser LLM agent exists yet (§8, still Checkpoint D's New Play screen): `PlaySpec` is built
  deterministically from `{objective, icp_overrides, target_count}` via
  `PlaySpec.model_validate({**icp_overrides, objective_text, target_count})` — the same shape a real
  parser would eventually hand this endpoint. `POST .../runs` persists the `Run`, calls `launch_run`,
  and returns 202 without awaiting it.
- `routers/runs.py` — `GET /runs/{id}`, `GET /runs/{id}/prospects`, and the SSE generator at
  `GET /runs/{id}/events`. The generator's only state is `last_seq`: fetch `repos.events.after(...)`,
  yield every row as one SSE frame (`id:`/`event:`/`data:`, the JSON `data` also carries `seq` so a
  client can persist the cursor independent of the SSE `id:` line), and if any rows came back, loop
  immediately without sleeping (independently-executing prospects can queue several rows between
  polls). Only once a poll returns *nothing* does it check whether the run is terminal — close cleanly
  if so, else emit a heartbeat comment at 15s and sleep 250ms. This ordering is what makes "replay
  everything, including anything written in the same instant the run finished" correct rather than a
  race.
- `routers/evaluation.py` — the one-route `GET /runs/{id}/evaluation` file the plan's file list names
  separately from `runs.py`, even though it nests under the same path prefix.
- `routers/prospects.py` — `GET /prospects/{id}` (the full aggregate: company, evidence, signals,
  score+dimensions+modifiers, contact, drafts, review, `agent_tasks` trace, approval state) and
  `POST /prospects/{id}/approve|reject`. Approve/reject insert one `approvals` row and return the
  refreshed aggregate — **the engine-computed `status` column is never overwritten by a human
  decision**; the human decision lives only in `approvals`, surfaced as `approval.state`
  (`PENDING`/`APPROVED`/`REJECTED`) computed from the latest row. Only a prospect whose engine status
  is `PASS`/`NEEDS_REVIEW`/`REJECTED` (i.e. it reached a review verdict) can be decided —
  `DUPLICATE`/`FAILED`/`TIMED_OUT`/still-running prospects get a 409. Nothing in this module — or
  anywhere in the codebase — calls email/LinkedIn/webhook providers; there is no such provider wired
  in at all, so there is structurally nothing for approve/reject to trigger.
- `routers/settings.py` — `GET /settings/providers`; reports `configured: bool` only, never a key
  value, per §21.

**`main.py`** — lifespan now calls `create_all()` (so a fresh clone's first `make api` doesn't 404 on
missing tables) and `RunRepository(SessionLocal).sweep_interrupted()` (honest crash recovery: a run
still `RUNNING` from a killed process is marked `INTERRUPTED` before the app accepts its first
request). All five routers included under `/api`; error handlers registered; the Checkpoint A health
check and CORS setup are untouched.

---

## Tests written and verified

All commands run from `apps/api/`. **63/63 passing** (`uv run pytest`, ~25s — up from Checkpoint B's
40/40 in ~4s; the added seconds are real `asyncio.sleep` jitter/backoff from running the actual demo
engine end-to-end through the HTTP layer repeatedly, not slow tests).

| File | Covers | Status |
|---|---|---|
| `test_api_plays_runs.py` | play creation from objective+overrides, live-mode rejection, 404s, **`POST .../runs` returns 202 in ~milliseconds without waiting for any prospect**, run reaches a terminal state with counters reconciling | ✅ (8) |
| `test_api_prospects.py` | prospect summaries expose board fields, full aggregate (score contributions sum to overall, evidence/trace present), approve is a pure state transition that never touches engine `status`, reject requires+records a reason, DUPLICATE/FAILED prospects 409 on approve/reject, 404s | ✅ (9) |
| `test_api_evaluation.py` | evaluation volume reconciles with the run's own `counters`, quality/reliability/guardrail fields are real fractions (not sentinels), all seven guardrail ids present, a run with no prospects yet returns `null` metrics rather than fabricated numbers | ✅ (3) |
| `test_api_sse.py` | persisted events emitted with strictly increasing `seq`, independently-executing prospects visibly interleave before any one of them completes, **disconnect mid-run + reconnect with `after_seq` loses nothing and replays nothing twice** (verified against a fresh from-zero replay), a completed run replays identically across two separate from-zero connections, unknown run 404s | ✅ (4) |

(Existing Checkpoint B suite — `test_health.py` through `test_run_integration.py`, 40 tests — is
unchanged and still green; see the original rows further up this file's git history if needed. Every
one of those files/tests still exists verbatim in `apps/api/tests/`.)

**Manual verification** (`uv run uvicorn groundwork.main:app --port 8010`, fresh `groundwork.db`):

1. `POST /api/plays` with the fixture pack's real ICP overrides → 201, `icp_spec` matches.
2. `POST /api/plays/{id}/runs` → **202 in 16ms** (`time curl`), confirming it does not wait.
3. `curl -N .../events?after_seq=0` piped through `head -n 20` (simulating a killed connection)
   captured seq 137–141 while `GET /runs/{id}` still read back `status: RUNNING` — genuinely mid-run.
4. Reconnected with `after_seq=141`: 131 more frames, seq range 142–272, **zero frames ≤ 141** (no
   replay), stream closed cleanly the instant `run.completed` (seq 272) was emitted and the run hit
   `PARTIAL`.
5. Reconnected twice more with `after_seq=0` after completion: both replays byte-identical (`diff`
   clean), 136 frames, ending in `run.completed`.
6. `GET /runs/{id}/prospects` and `GET /prospects/{id}` returned real per-company data (scores,
   contacts, signals, trace) — spot-checked against the same run's fixture inputs.
7. `GET /runs/{id}/evaluation` — every field a real computed fraction/count (see the JSON captured
   during this session); re-running with a play whose `icp_overrides` omitted `excluded_industries`
   produced **Cobalt Retail Systems scoring `PASS` instead of the fixture demo's `REJECTED`** — direct
   proof scoring/evaluation are computed from *this run's* `PlaySpec`, not memoized or hardcoded per
   company.
8. `POST /prospects/{id}/approve` → `approval.state: APPROVED`, actor recorded, `status` (engine
   column) unchanged at `PASS`. `POST` on a `DUPLICATE` prospect → 409 RFC-7807 body. `GET` on an
   unknown id → 404 RFC-7807 body.
9. Killed the uvicorn process mid-run (`SIGTERM`), restarted it against the same `groundwork.db`:
   the previously-`RUNNING` run came back `INTERRUPTED` on the very next `GET /runs/{id}`, confirming
   the startup sweep runs before the app serves its first request.

| File | Covers | Status |
|---|---|---|
| `test_health.py` | Checkpoint A health check | ✅ (1) |
| `test_dedupe.py` | domain normalization, legal-suffix stripping, key precedence | ✅ (5) |
| `test_grounding.py` | token overlap, grounded/ungrounded claims, cross-prospect citation rejection | ✅ (7) |
| `test_scoring.py` | weights sum to 1.0, full-evidence high score, hard disqualifier caps at 25, unsupported dimension contributes 0, same-input-same-score, confidence = coverage, industry/size boundary cases | ✅ (8) |
| `test_review.py` | all seven checks individually, hard→FAIL / soft→NEEDS_REVIEW / clean→PASS, unverified contact + email is a hard fail, hard outranks soft | ✅ (11) |
| `test_fixture_provenance.py` | no fixture source is a URL, every fixture row has title/claim/snippet, the model validator rejects a fake URL on `DEMO_FIXTURE` *and* `LLM_INFERENCE`, and requires one on `LIVE_FETCH` | ✅ (6) |
| **`test_isolation.py`** | **two confusable prospects, unique canary tokens, run through the real engine concurrently; asserts zero cross-contamination in evidence, signals, score explanation and outreach** | ✅ (1) |
| `test_run_integration.py` | full 7-prospect headless run: exact status distribution, ≥1 retry recorded, Quarry's research retries genuinely exhausted, Northwind's score deterministic and reproducible, duplicate correctly links `duplicate_of`, `run_events` present and strictly ordered | ✅ (1) |

**Headless demo verification** (`make demo-reset && make demo`, seed=42):

```
DUPLICATE: 1   FAILED: 1   NEEDS_REVIEW: 2   PASS: 2   REJECTED: 1
run status: PARTIAL
retries recorded: 3
```

Northwind Labs (PASS, score 92) retried once on research (`ProviderTimeout` → succeeded on attempt 2).
Quarry Systems (FAILED) exhausted all 3 research attempts (`ProviderUnavailable`) without affecting
any sibling prospect. Confirmed reproducible across repeated `make demo-reset && make demo` runs.
**No outcome is hardcoded** — every status above is the runner's own `_derive_final_status()` reading
`ctx.score.disqualified` and `ctx.review.verdict`, both computed from fixture evidence at run time.

---

## Known issues / deviations from plan

- **`stable_seed()` instead of `hash()`** for seeded jitter (see `providers/base.py`) — Python's
  builtin `hash()` on strings is randomized per process, so the plan's illustrative
  `random.Random(hash((run_id, prospect_id, step_name)))` would not actually reproduce across runs.
  Using a sha256-based digest instead so `--seed` on `run_demo.py` is genuinely replayable.
- **`SearchProvider.fetch_sources` takes an extra `ctx_key` parameter** beyond the plan's §11
  Protocol sketch, needed to scope scripted-failure attempt counts and jitter per `(run_id,
  prospect_id, step)` rather than per-company-globally. `LLMProvider.structured` already had
  `ctx_key` in the plan, so this just makes both Protocol methods consistent.
- **No separate `SignalExtractionOutput` LLM call.** The plan describes signal detection as Hybrid
  (LLM proposes, deterministic verifier confirms); implemented as: Research's LLM call *is* the
  "propose" half (it returns claim text per fact), and `engine/steps/signals.py` is the deterministic
  verifier, with no second LLM round-trip. Keeps the engine smaller without weakening the
  hybrid-verification claim — the demotion-on-ungrounded-claim mechanism is real and is what makes
  Riverbend Analytics land `NEEDS_REVIEW` from actual token-overlap failure, not a scripted flag.
  `models/llm_io.py` documents this in `ResearchExtractionOutput`'s docstring.
- **`domain/scoring.py`'s `explanation` field is filled by a real (demo) LLM call**, wired in
  `engine/steps/score.py`, honoring the "LLM writes prose from the numbers, never changes them" claim
  — `ScoreExplanationOutput` only carries a string, and the envelope's metadata carries only the
  already-computed `overall`/`top_dimensions`, so the model has no numbers to alter even if it tried.
- **Objective Parser LLM agent (§8) is not built.** It's a New Play *screen* concern (Checkpoint D);
  `run_demo.py` and `seed.py` load the fixture pack's own authored `PlaySpec` directly, same as the
  demo pipeline will eventually receive from the parser. No engine code depends on it existing yet.
- **Core engine machinery is ~484 LOC** (`context.py` + `step.py` + `pipeline.py` + `runner.py`,
  including docstrings), slightly over the "~400 LOC" figure in §30/§33. That figure's stated purpose
  — preventing a generic DAG/plugin framework — was preserved: this is a fixed 7-step list, one retry
  wrapper, and one semaphore-bounded fan-out, not a framework. Not considered worth trimming further
  at the cost of the documentation that makes this reviewable without conversation history.
- **`run.started`/`run.completed` are now emitted** (resolved in Checkpoint C) — from
  `api/run_service.py`, wrapping the call to `execute_run()`, not from inside `engine/runner.py`
  itself (keeping that file untouched per Checkpoint B's "do not touch"). `run.failed` is emitted if
  `execute_run` raises before/around the per-prospect fan-out. **`plan.created` is still not
  emitted** — there is no per-run planning step to report on: the pipeline is the same fixed
  7-step list every run (§10's whole point), so `RunRow.plan` stays `[]`. If a later checkpoint wants
  a rendered plan (e.g. for a "Plan" panel in the UI), that's `Pipeline.steps` names written once at
  run creation, not a new engine capability.
- Sable Compute (7th, optional fixture) was added per the plan's explicit allowance ("if six are
  comfortably complete and cost nothing to extend"); Halden Systems (the 8th, also optional) was not
  added — not required for the acceptance distribution and the checkpoint clock took priority.
- Nothing about Checkpoint A is affected or altered.

**Checkpoint C's own deviations:**

- **No Objective Parser LLM agent (§8) — still true, as anticipated.** `POST /plays` builds `PlaySpec`
  deterministically: `PlaySpec.model_validate({**icp_overrides, objective_text: objective,
  target_count})`. `icp_overrides` is exactly the shape a real parser's structured output would
  eventually fill in (target industries, size band, funding stages, etc.) — this endpoint doesn't
  change shape when the parser is built later, only what populates `icp_overrides` does. Verified live:
  a play built with empty `excluded_industries` scored Cobalt Retail Systems `PASS` instead of the
  fixture demo's `REJECTED` — proof the whole scoring/review chain runs off *this run's* `PlaySpec`,
  not a memoized fixture-pack result.
- **No `RunRegistry` class, despite §17 describing one.** `api/run_service.py` keeps a bare
  `set[asyncio.Task]` purely so `asyncio.create_task(...)` results aren't garbage-collected
  mid-flight — the classic footgun, not a status store. Every reader (`GET /runs/{id}`, the SSE
  generator, evaluation) reads run/prospect state straight from their tables, same as a process that
  restarted and lost all in-memory state would have to. This is arguably *more* honest than a
  registry that could drift from the DB, and required no new abstraction.
- **Human approval never touches the engine-owned `ProspectRow.status` column.** §21 says "update the
  appropriate state"; the appropriate state here is a new, separate signal
  (`approvals.decision`, surfaced as `approval.state`), not an overwrite of the review-computed
  status. Overwriting `status` would have destroyed the "why did the engine land here" record the
  whole `review_results` chain exists to preserve, and would have required inventing new
  `ProspectStatus` enum values (`APPROVED`/etc.) that don't fit the engine's own vocabulary. A
  prospect's `status` and its `approval.state` are two different axes: one is what the pipeline
  concluded, the other is what a human decided about that conclusion.
- **Only `PASS`/`NEEDS_REVIEW`/`REJECTED` prospects are decidable** (409 otherwise). Not stated
  explicitly in §21, but implied by "state transitions" needing a state to transition *from* — a
  `DUPLICATE` or still-`RUNNING` prospect never reached a review verdict for a human to weigh in on.
- **`ContactRow` has no uniqueness constraint** (unlike `ICPScoreRow`, which is `unique=True` on
  `prospect_id`) — the pipeline only ever writes one per prospect, but `get_contact` defensively
  orders by `id DESC` and takes the first row rather than assuming exactly one exists.
- **Live Mode is rejected at the API layer** (`POST /plays/{id}/runs` returns 422 if `mode != "demo"`)
  before ever reaching `providers/registry.py`'s own `NotImplementedError` for `Mode.LIVE` — cleaner
  error message, same underlying constraint. `PlayCreateRequest.mode` is typed `Literal["demo"]`, so
  `mode: "live"` on `POST /plays` itself is already a 422 from Pydantic before the route body runs.

---

## Next task

**Checkpoint D — Hero product UI** (`docs/IMPLEMENTATION_PLAN.md` §30, budget 70m). The API is fully
verified from curl (see this session's manual verification above) — build `components/ui/*` (8
hand-rolled primitives), `lib/{types,useRunStream,format}.ts`, `app/plays/new/page.tsx`,
`app/runs/[id]/page.tsx`, and `components/{RunBoard,ProspectRow,ActivityStream,PlanPanel}.tsx`.
`lib/useRunStream.ts` should mirror exactly what `test_api_sse.py` proved: keep `lastSeq`, reduce over
event `type`, and on reconnect or `run.completed` do one authoritative `GET /runs/{id}/prospects`
refetch to reconcile (§19). Acceptance is demo checklist items 1–5 (§32): rows advance independently
at different rates, the retry and the failure are visible, refresh mid-run is correct, counters
reconcile at completion. **Do not build**: dashboard home, prospects table, settings UI, animations
beyond a stage-change transition, the Quality tab's contents (that's Checkpoint F).

---

## Do not touch (finished areas)

- Everything under **Known issues / deviations** above — those are deliberate, not oversights.
- `apps/api/groundwork/domain/*.py` — pure, unit-tested, and the four modules the project's whole
  audit-chain story rests on. Changing scoring weights/formulas or review check semantics is a scope
  decision — surface it, don't silently drift.
- `apps/api/groundwork/engine/{context,step,pipeline,runner}.py` — the isolation/concurrency/retry
  machinery. Extend via new steps or repositories, not by restructuring the fan-out or replacing
  `gather(return_exceptions=True)`.
- `apps/api/groundwork/fixtures/demo_pack.yaml` — the acceptance distribution
  (`test_run_integration.py`) is keyed to this exact evidence. Editing a company's evidence can change
  its computed outcome; re-run `make test` and `make demo` after any edit here.
- `apps/api/groundwork/models/schemas.py`'s `Evidence._no_fake_sources` validator and
  `tests/test_fixture_provenance.py` — the provenance invariant. Do not weaken either without
  explicit approval.
- `apps/api/groundwork/api/*.py` and `apps/api/groundwork/evaluation/metrics.py` (Checkpoint C) —
  every P0 endpoint from §21 is implemented and tested; do not add `POST /runs/{id}/cancel`,
  draft-editing, or regeneration endpoints (explicitly P1) without the user asking. The SSE polling
  interval (250ms) and heartbeat interval (15s) are the plan's own stated values — don't "optimize"
  them without a reason.
- The approve/reject **state-transition boundary**: `approvals` rows are additive audit history, and
  `ProspectRow.status` is engine-owned. Do not add a code path that writes to `ProspectRow.status`
  from `routers/prospects.py`, and do not wire any email/LinkedIn/webhook provider behind
  approve/reject without the user explicitly asking for that scope change — it's the one invariant
  Checkpoint C exists partly to demonstrate is *absent*.
- Checkpoint A's `do not touch` list (`main.py`/`config.py`/`db.py`'s pre-existing shape, `apps/web/`)
  still applies; `config.py`/`db.py` grew as anticipated (WAL pragma already existed from Checkpoint A
  and is unchanged; added `create_all()`/`drop_all()` only).
