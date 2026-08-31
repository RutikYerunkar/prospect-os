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
| **C — API / SSE** | `21a615e` (merged to `master` via PR #3) | FastAPI routers for every P0 endpoint, async run launch (202), resumable SSE over `run_events`, computed-on-read evaluation metrics, approve/reject as state transitions, tests. |
| **D — Hero product UI** | `aa41f97` (merged to `master` via PR #4) | New Play (objective + 4 controls + live-parsed read-only `PlaySpec`), Run Detail hero screen (live board, activity stream, counters, bounded-concurrency indicator, Board/Quality tab shell), `useRunStream` SSE client with manual reconnect + REST reconciliation, minimal Prospect Detail placeholder. No backend changes. |
| **E — Depth UI** | `a1f9190` (merged to `master` via PR #5) | Full `/prospects/{id}` page: score breakdown table (reconciles to the displayed overall), evidence cards with provenance chips, grouped signal list, contact/buyer panel, outreach viewer with grounded-claim references, all-seven-checks review panel, approve/reject wired to the existing audit-trail endpoints, execution trace table with independently visible retries. No backend changes. |
| **F — Quality + hardening** | `41883be` (merged to `master` via PR #6) | Quality tab (`MetricGrid` + `GuardrailPanel`) backed by the existing evaluation endpoint; a real demo-consistency bug found and fixed (New Play's default ICP overrides silently diverged from the fixture pack, changing both prospect count and Northwind Labs' score); visual polish (friendlier terminal states, humanized activity labels, obvious synthetic-evidence badges, structural-dimension score clarity); two clean-reset rehearsals through the real UI; README + DEMO_SCRIPT finalized. **P0 COMPLETE.** |
| **G — Live Mode LLM provider** | *this commit* (branch `claude/checkpoint-g-live-mode-bdtavb`) | **REAL OpenAI LLM + FIXTURE SEARCH** — `LIVE LLM · FIXTURE SEARCH`. Real `OpenAILLMProvider` (Responses API, strict Structured Outputs, `store=False`) behind the same `LLMProvider` Protocol Demo Mode already satisfies; process-scoped `LiveProviderRuntime`; a flat (never nested) retry loop bounded at `1 + T + S = 4` attempts with full per-attempt telemetry persisted to a new `llm_calls` table; the Objective Parser as the fourth Live LLM operation, with deterministic fallback and transactional Play+telemetry persistence; a soft per-run cost budget; hard cost/concurrency/prospect-count bounds; central secret redaction. Demo Mode preserved byte-identical at every gate. |

---

## Current checkpoint

**G — Live Mode LLM provider.** Demo Mode (Checkpoints A–F) remains P0-complete and untouched at the
domain/output level. Checkpoint G adds real OpenAI execution behind the existing provider seam — see
"What Checkpoint G added" below for the full implementation, and "Next task" for what's explicitly
*not* started (Checkpoint H — live web search).

A future session should read `CLAUDE.md`, `docs/ARCHITECTURE.md`, and this file before starting any
further work. **Do not begin Checkpoint H** without the user explicitly asking.

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

## What Checkpoint D added

Frontend only — zero backend files touched (verified: `git status` on this branch shows only
`apps/web/**` and this doc changed). All API/SSE behavior consumed as-is from Checkpoint C.

**`apps/web/components/ui/`** (8 hand-rolled primitives, no shadcn) — `Card`, `Panel` (titled section
container), `Badge` (semantic tone map: emerald/amber/rose/sky/indigo/neutral), `Button`
(primary/secondary/ghost), `Table`/`THead`/`TBody`/`TR`/`TH`/`TD`, `Progress`, `Tabs`/`Tab`, `Stat`.
Visual language locked once in `app/globals.css` + `app/layout.tsx`: `zinc-950` ground, `zinc-900`
surfaces, `zinc-800` hairline borders, one accent (`indigo-400`), JetBrains Mono (via
`next/font/google`) for identifiers/scores/durations, system sans for prose, no gradients/glows.

**`apps/web/lib/`**
- `types.ts` — hand-mirrored wire types for every DTO in `groundwork/api/schemas.py` (`PlaySpec`,
  `PlayResponse`, `RunResponse`, `ProspectSummary`, `RunEvent`, etc.) plus `PIPELINE_STAGES` (the
  7-stage order for the board's stage track). No codegen, per §18.
- `format.ts` — duration/elapsed/time/stage/status/score/confidence formatting helpers.
- `constants.ts` — `MAX_CONCURRENT_PROSPECTS = 3`, documented as mirroring
  `Settings.max_concurrent_prospects` in `config.py` (not returned by any Checkpoint C endpoint — the
  concurrency *bound* shown next to "Agents active" is this constant; the numerator is always computed
  live from real prospect stage data, never hardcoded).
- `api.ts` — extended with `apiPost`, `eventStreamUrl`, and typed wrappers (`createPlay`, `startRun`,
  `getRun`, `listRunProspects`) over the existing `apiGet`/`ApiError`.
- `useRunStream.ts` — the SSE client. Bootstraps via `Promise.all([getRun, listRunProspects])` (REST,
  authoritative) before opening `EventSource(.../events?after_seq=<lastSeq>)`. Listens for each of the
  11 documented event types individually (`addEventListener` per type, since the backend names the SSE
  `event:` field), applies lightweight reducer updates for immediate feedback (stage advances, retry
  badges, terminal status), and schedules a debounced authoritative `GET /runs/{id}/prospects` refetch
  on `prospect.scored`/`prospect.reviewed`/`prospect.completed`/`run.completed`/`run.failed` — fields
  like score/contact/review only ever land through the aggregate read, never invented from an event
  payload. **Reconnect is manual, not the browser's built-in `EventSource` retry**: on `error` the
  client closes the socket itself and reopens with `after_seq=<lastSeqRef>` (exponential backoff,
  capped at 8s) — relying on the native retry would replay from the original `after_seq` and duplicate
  every event already applied. `run.completed`/`run.failed` set the known-terminal ref *synchronously*
  from the event payload (not from the async `GET /runs/{id}` refetch) specifically so the clean
  server-side stream close that follows isn't mistaken for a drop and doesn't trigger a pointless
  reconnect — this raced in early manual testing before the fix.

**Pages**
- `app/plays/new/page.tsx` — objective textarea (seeded with the plan's own §4 demo objective) + chip
  input for target industries + size band min/max + minimum ICP score + prospect count. Debounces
  (600ms) a live call to the real `POST /api/plays` as the form changes, rendering the returned
  `PlaySpec` read-only via `PlanPanel` beside the form — there is no separate parse-only endpoint, so
  this *is* how the API parses an objective (§8's Objective Parser LLM agent still doesn't exist; the
  UI states this plainly: "objective parsing is deterministic in this checkpoint, not an LLM call").
  **Run Agents** re-parses only if the form changed since the last successful parse (tracked via a
  signature ref, not a `useState` flag — the naive version tripped
  `react-hooks/set-state-in-effect`), then calls `POST /plays/{id}/runs` and navigates to
  `/runs/{run_id}` immediately on the 202.
- `app/runs/[id]/page.tsx` — the hero screen. `RunSummary` header (run id, status, mode chip, SSE
  connection-state chip, live elapsed timer, objective text fetched separately via `GET
  /plays/{play_id}` since `RunResponse` doesn't carry it, progress bar, and the counter row: discovered
  / **agents active N / 3** / completed / pass / needs review / rejected / duplicate / failed — all
  derived from the live `prospects` array, never from `run.counters`, which is `{}` until the backend
  finalizes the run). Board/Quality `Tabs` shell — Quality is the required placeholder string, no
  metrics logic. `RunBoard` renders `ProspectRow`s in a `Table`; `ActivityStream` renders the newest
  event first from real SSE payloads (`describeEvent()` switches on `event.type`, no manufactured
  frontend-only events).
- `app/prospects/[id]/page.tsx` — minimal placeholder per the explicit Checkpoint D scope restriction;
  links back to New Play. Checkpoint E replaces this file's contents entirely.
- `app/page.tsx` — now a server-side `redirect("/plays/new")` (was the Checkpoint A health-check demo
  card); no global dashboard/prospects/settings pages were added, per scope.

**Components** — `PlanPanel` (read-only `PlaySpec` grid, reused by New Play's preview), `RunSummary`,
`RunBoard`, `ProspectRow` (per-row `StageTrack` — 7 dots across `PIPELINE_STAGES`, filled for
done/current-pulsing-sky/upcoming-zinc; retry badge sourced from `useRunStream`'s live `retrying` map
when a step is currently mid-retry, falling back to a persistent "↻ retried" badge from
`prospect.had_retry` once it resolves so the retry stays visible without dev tools per the task spec),
`ActivityStream`.

**Concurrency legibility, concretely:** a prospect's row shows `queued` (grey, no stage dots lit) from
creation until its coroutine actually acquires the semaphore slot and starts `Research` — the "Agents
active" numerator counts only rows whose `stage !== DISCOVERED` and whose status isn't yet terminal.
With `max_concurrent_prospects=3` and 6 fixture prospects, this reliably shows at most 3 rows advancing
at once, which is the whole point of the board per §4's demo narrative.

---

## What Checkpoint E added

Frontend only — zero backend files touched (`git status`/`git diff --stat` on this branch shows only
`apps/web/{app/prospects/[id]/page.tsx,lib/{api,format,types}.ts}` modified and seven new
`components/*.tsx` files). The full `ProspectAggregate` served by `GET /api/prospects/{id}`
(Checkpoint C) needed nothing new on the backend — every section below reads straight off it.

**`apps/web/lib/types.ts`** — hand-mirrored wire types for the aggregate: `EvidenceItem`, `SignalItem`,
`DimensionScore`, `ScoreModifier`, `ProspectScore`, `ProspectContact`, `ClaimMapEntry`,
`OutreachDraft`, `ReviewCheck`/`ReviewResult`, `AgentTaskTrace`, `ApprovalInfo`, `ProspectCompany`,
`ProspectAggregate`. Verified field-by-field against `groundwork/api/routers/prospects.py`'s
`_evidence_dict`/`_signal_dict`/`_score_dict`/`_contact_dict`/`_draft_dict`/`_review_dict`/`_task_dict`
and the underlying domain models (`DimensionScore`, `ScoreModifier`, `ReviewCheck` in
`groundwork/models/schemas.py`) rather than guessed — e.g. `dimensions`/`modifiers`/`checks`/
`claim_map` are `model_dump(mode="json")` lists of those exact shapes (confirmed in
`repositories/prospect_data.py`).

**`apps/web/lib/api.ts`** — `getProspect`, `approveProspect`, `rejectProspect` typed wrappers over
`GET/POST /api/prospects/{id}[/approve|reject]`, following the existing `apiGet`/`apiPost` pattern.

**`apps/web/lib/format.ts`** — added `formatDateOnly` for signal `occurred_at`.

**`apps/web/components/`** (seven new files, per the checkpoint's own file list):
- `ScoreBreakdown.tsx` — the arithmetic table (dimension/raw/weight/contribution/evidence/support),
  a Σ row, and a reconciliation line computed **in the component itself** from the dimension array
  (`Math.round(Σ contribution × 100)`) — not trusted from a separate backend field — so the "72 not
  65" claim is checkable against the same numbers the table shows. When `score.disqualified` and the
  rubric total differs from the displayed `overall`, it renders the cap explicitly
  (`rubric total X → capped to Y`) instead of silently showing only the capped number. Confidence is
  a separate `Stat`, never blended into the score.
- `EvidenceCard.tsx` — origin decides the render, not a convention: only `origin === "LIVE_FETCH" &&
  source_url` ever produces a clickable `<a>`; `DEMO_FIXTURE` renders "Synthetic evidence · demo
  fixture", `LLM_INFERENCE` renders "Model inference · unsourced" with a dashed border so inferred
  assertions read differently from sourced-but-synthetic evidence, per the task's explicit
  requirement.
- `SignalList.tsx` — groups signals sharing an identical `(type, summary)` pair into one row with a
  `×N` badge. This is a rendering-only decision, not a data change: research extraction can produce
  several structured facts (e.g. three separate GTM `HiringRole` entries) from one source sentence,
  each persisted as its own `Signal` row with the same summary text — confirmed real via
  `GET /api/prospects/{id}` on Northwind Labs (three identical `HIRING` rows, one `TECH` claim ×3).
  The ICP score's own `hiring_signal`/`tech_fit` dimensions already dedupe evidence ids via a `set()`
  in `domain/scoring.py`, so this UI grouping doesn't change what's scored — it just stops the page
  from showing three copy-pasted lines. Ungrounded signals (`evidence_ids: []`, e.g. Riverbend's
  demoted funding claim) render a `"ungrounded — demoted, does not score"` badge instead of a fabricated
  evidence link.
- `ContactPanel.tsx` — `UNAVAILABLE` renders explicit copy ("intentional outcome, not a missing
  field") rather than empty space that could read as a bug.
- `OutreachViewer.tsx` — renders every draft sorted by `step_index`, with its `claim_map` entries and
  the evidence titles each grounded claim resolves to (or an `unsupported` badge if a claim carries no
  evidence ids — the same condition `claim_grounding` checks).
- `ReviewPanel.tsx` — all seven checks always rendered, including passes; verdict badge up top.
- `TraceTable.tsx` — one row per `agent_tasks` attempt, sorted by `started_at`; a retry sequence
  (`RETRY` → `RETRY` → `FAILED`, or `RETRY` → `OK`) stays as separate rows by construction, since the
  backend already records one row per attempt — nothing here collapses them. A small inline bar scales
  each row's duration against the max duration in that prospect's trace.

**`apps/web/app/prospects/[id]/page.tsx`** — replaces the Checkpoint D placeholder. Header (company,
domain, status badge, score/confidence/contact-verification at a glance, back-to-run link,
duplicate-of link for `DUPLICATE`, pipeline error text for `FAILED`), then the seven sections in the
task's suggested order (score → signals+evidence → contact → outreach → review → **approval** →
trace — approval placed after review since a decision naturally follows the checks, not one of the
seven content sections itself). `ApprovalBar` calls `approveProspect`/`rejectProspect` and sets page
state directly from the POST response (the aggregate the backend just recomputed) rather than
optimistically mutating — satisfies "update the UI from the authoritative backend response" without a
second round-trip. Buttons disable/disappear once `approval.state !== "PENDING"` or the prospect's
engine `status` isn't in `{PASS, NEEDS_REVIEW, REJECTED}`, mirroring the backend's own
`_DECIDABLE_STATUSES` 409 boundary client-side so an invalid transition is never offered, not just
rejected after the fact. Reject requires a non-empty reason (matches the backend's `min_length=1`).
Loading/error states follow `app/runs/[id]/page.tsx`'s exact pattern (effect-only fetch with a
`cancelled` guard, not a `useCallback`-wrapped function called synchronously in the effect body — the
latter trips `react-hooks/set-state-in-effect`, hit and fixed during this checkpoint).

---

## What Checkpoint F added

**Backend** — one additive field, no invariant touched. `apps/api/groundwork/evaluation/metrics.py`
gained `reliability.per_step_success_rate` (a `dict[step_name, float]` computed from the same
`agent_tasks` rows already loaded — a step "succeeds" if any attempt for a given `(prospect_id,
step_name)` pair reached `OK`, counted over distinct pairs so a 3-attempt retry sequence doesn't dilute
the rate). Nothing existing changed shape; `test_api_evaluation.py`'s assertions are all still exact
matches on the fields they already checked.

**`apps/api/groundwork/models/schemas.py` / `apps/api/groundwork/api/schemas.py`** —
`PlaySpec.target_count` and `PlayCreateRequest.target_count` defaults changed `6 → 7`, to match the
fixture pack's own canonical count (`demo_pack.yaml`'s `play_spec.target_count: 7`, already what
`tests/api_helpers.py::create_play` and `test_api_evaluation.py` assert). This was the task's own
named presentation inconsistency ("UI asks for 6, Demo Mode intentionally returns 7") — resolved by
making the default truthful rather than special-casing Sable Compute out of the count anywhere.

**Frontend — Quality tab** (`apps/web/components/MetricGrid.tsx`, `GuardrailPanel.tsx`,
`QualityTab.tsx`, new; `lib/types.ts`/`lib/api.ts` extended with `RunEvaluation` and
`getRunEvaluation`): replaces the Board-tab-only placeholder string in `app/runs/[id]/page.tsx` with a
real dashboard backed **only** by `GET /api/runs/{id}/evaluation` — Volume (discovered / completed /
PASS / NEEDS_REVIEW / REJECTED / DUPLICATE / FAILED, all read from `by_status` where possible so the
label matches the real enum, not a re-derived approximation), Grounding/Quality (evidence coverage,
grounded-claim rate, dimension support, unsupported-claim count, mean score/confidence, contact
verification breakdown, provenance mix with an explicit "100% synthetic" callout when every evidence
row is `DEMO_FIXTURE`), Reliability (retries, p50/p95 duration, run wall time, per-step success rate,
provider error breakdown), and a `GuardrailPanel` showing all seven checks' pass rates with
click-through links to the specific prospects that failed each. Every label carries an explanatory
`title` tooltip stating how it's computed, per the plan's "no fabricated benchmark numbers, tooltip on
every metric" requirement. Polls every 2s only while the run is still `RUNNING` (so opening the tab
mid-run stays live); a single fetch is enough once terminal. No decorative charts — cards, compact
tables, and small progress bars only, matching the task's "readable in under 45 seconds" constraint.

**Frontend — visual polish** (no design-system change; §18's locked palette/typography untouched):
- `RunSummary.tsx`: raw run status now renders through a new `formatRunStatus()` (`lib/format.ts`) —
  `PARTIAL` → "Completed with issues" — with the true raw enum value still available via a `title`
  tooltip on the badge, never discarded. The connection-state badge (`live`/`reconnecting`/`stream
  closed`/etc.) is now hidden entirely once the run reaches a terminal status, instead of showing
  "stream closed" next to a finished run in a way that could read as an error.
- `ActivityStream.tsx`: added the missing `prospect.stage_changed` case (was previously falling through
  to the raw `"{company} · prospect.stage_changed"` machine label — a real instance of the bug the task
  named) rendering as `"{company} · advanced to {stage}"`; the generic fallback for any future
  unhandled event type now humanizes dots/underscores instead of printing the raw type verbatim;
  `run.completed`'s line now reuses `formatRunStatus()` too, so "run completed · Completed with issues"
  matches the header badge instead of the raw enum.
- `EvidenceCard.tsx`: `DEMO_FIXTURE` evidence now carries an explicit `SYNTHETIC` badge next to the
  signal-type/confidence badges (not just the existing muted footer caption) — scannable at a glance
  across a whole evidence list, per the task's explicit "make this obvious enough to point to in the
  interview" requirement.
- `ScoreBreakdown.tsx`: `industry_fit`/`size_fit` (the two dimensions `domain/scoring.py` documents as
  always structurally supported from `CompanySeed`, never evidence-gated) now render "supported ·
  profile" with `profile` in the Evidence column instead of a bare `0` — resolves the apparent
  contradiction of "supported" next to "0 evidence" without changing any scoring semantics; every other
  dimension's evidence-gated Support/Evidence columns are unchanged.
- Several standalone help/error strings bumped `text-zinc-600 → text-zinc-500` for readability against
  the `zinc-950` background (QualityTab's computed-on-read note and error detail, MetricGrid's
  empty-state lines, ReviewPanel's explainer line, ApprovalBar's helper/error lines, New Play's mode
  note, both pages' `loadError` detail lines, ActivityStream's timestamp column). Decorative `—`
  placeholders (ProspectRow, PlanPanel) were deliberately left at their original muted tone — they're
  null-markers, not prose a founder needs to read.
- `app/icon.svg` and `app/favicon.ico` (a hand-built minimal 16×16 ICO, no image tooling available in
  this environment — written directly via `struct` in Python) added; there was no favicon in the repo
  before this checkpoint, which meant every single page load logged a real browser-console 404 —
  caught during rehearsal, not a hypothetical.

**Frontend — demo consistency, the real bug found during rehearsal**
(`app/plays/new/page.tsx`): the New Play form's `overrides()` function only ever sent
`target_industries`/`size_band_min`/`size_band_max`/`min_score` — the four controls the form exposes —
and silently omitted `excluded_industries`, `adjacent_industries`, `target_funding_stages`,
`target_technologies`, `persona_titles`, and `min_confidence` entirely, defaulting them to empty/zero
via `PlaySpec`'s own Pydantic defaults. Two concrete, verified consequences of this, caught by actually
running the canonical play through the browser (not curl) during rehearsal:
1. **Cobalt Retail Systems scored `PASS` instead of the fixture's intended `REJECTED`** — its hard
   disqualifier (`retail_pos` on the exclude list) never fired because `excluded_industries` was never
   sent. This silently broke checklist item 5 of §32 ("see a mix of outcomes — pass, needs-review,
   rejected, duplicate, failed") for anyone using the actual product UI rather than the test suite's
   `DEMO_ICP_OVERRIDES`.
2. The default `industries` chip was the human-readable `"AI Infrastructure"`, not the fixture
   companies' actual `industry` slug (`"ai_infrastructure"`); `domain/scoring.py::_industry_fit` matches
   by exact string, so every fixture company's `industry_fit` dimension silently downgraded from a full
   `1.0` match to a `0.6` adjacent-match — Northwind Labs scored **84** through the UI instead of the
   documented, tested **92**.

Fixed by sending the full canonical ICP override set (matching `tests/api_helpers.py::DEMO_ICP_OVERRIDES`
and the fixture pack's own `play_spec` exactly) as defaults, with the four exposed form controls still
overriding their corresponding fields — and by changing the default size band (`1–500 → 50–250`, the
fixture's own band) and the default industry chip (`"AI Infrastructure" → "ai_infrastructure"`).
Re-verified twice from a clean `make demo-reset`: the play created through the actual New Play UI now
reproduces the exact documented reference numbers byte-for-byte (Northwind Labs 92, Riverbend Analytics
35, Cobalt Retail Systems 25, Ferrous Grid 58, Sable Compute 79; `PASS ×2 / NEEDS_REVIEW ×2 / REJECTED
×1 / DUPLICATE ×1 / FAILED ×1`), matching `make demo`'s headless output and this file's own previously
recorded Checkpoint E numbers.

**Docs** — `README.md` rewritten from the one-line stub into a founder/recruiter-facing document
(thesis, Mermaid architecture diagram, deterministic-vs-LLM table, evidence/provenance model,
concurrency/isolation explanation with the real `execute_run` snippet, demo-vs-live-provider boundary,
local setup, `make` commands, an explicit "what's real vs. synthetic" table, and a short
production-scaling discussion). `docs/DEMO_SCRIPT.md` filled in from its stub with the full 5–6 minute
walkthrough (exact 13-beat sequence), a 2-minute shortened version, the seven strongest one-line
answers to the founder discussion questions, and a rehearsal-notes section recording the demo-bug find
above and the exact reproducible numbers.

---

## What Checkpoint G added

**Goal:** real LLM execution in Live Mode while keeping fixture-backed search — `LIVE LLM · FIXTURE
SEARCH`. No live web search (Checkpoint H). Demo Mode's domain/output-level behavior is unchanged;
verified byte-identical at every phase gate (see Verification below).

**Phase 1 — provider-neutral telemetry/error seam** (`groundwork/providers/base.py`, rewritten):
`LLMOperation` (`research_extraction`/`score_explanation`/`personalization`/`objective_parse`),
`LLMAttemptKind` (`initial`/`transport_retry`/`schema_repair`), `LLMAttemptStatus` (12 values per the
spec), `LLMAttemptTelemetry` (one provider attempt — timing, tokens, reasoning tokens, cost, HTTP
status, provider request id, incomplete reason, redacted error/validation text, digests), generic
`LLMResult[T]` (`.parsed: T` — callers never re-`model_validate()` a raw dict; kept `.data`/
`.tokens_in`/`.tokens_out` as backward-compatible properties collapsed from the final attempt). Typed
errors: `ProviderTimeout`/`ProviderUnavailable`/`ProviderRateLimited` (step-retryable — `STEP_RETRYABLE`
tuple), `SchemaViolation`/`ProviderRefusal`/`ProviderOutputTruncated`/`ProviderContentFiltered`/
`ProviderAuthError`/`ProviderNotConfigured`/`ProviderBudgetExceeded` (permanent). Every `ProviderError`
carries `.attempts: list[LLMAttemptTelemetry]` accumulated up to the point of failure.
`ProviderBundle.provider_semaphores` — confirmed dead (only ever written in `registry.py`, never read
by any step) — **deleted**, not preserved.

`groundwork/engine/budget.py` (new): `PipelineBudget` — the injectable set of per-step
timeout/retry/backoff constants `build_prospect_pipeline()`/`Step` used to hardcode.
`DEMO_BUDGET = PipelineBudget()` reproduces Checkpoint B–F's literals exactly (`timeout_s=2.0`,
research `max_retries=2`, personalize `max_retries=1`, `backoffs_s=(0.4, 0.8, 1.6)`). `Step` gained a
`backoffs_s` field (default `BACKOFFS_S`, unchanged); `build_prospect_pipeline(budget=DEMO_BUDGET)` now
threads every timeout/retry/backoff from `budget` instead of hardcoding. `execute_run(..., budget:
PipelineBudget = DEMO_BUDGET)` — the existing `max_concurrent_prospects`/`run_wall_clock_timeout_s`
params (already injected since Checkpoint B) are unchanged and still authoritative for those two;
`budget` only supplies the per-step values `build_prospect_pipeline` needs. `api/run_service.py`
builds a `live_budget_from_settings()` (`LIVE_STEP_TIMEOUT_S=45`, `LIVE_RUN_WALL_CLOCK_TIMEOUT_S=600`)
entirely outside `engine/`.

**Phase 2 — real prompts** (`groundwork/prompts/`, new package): `base.py` (token-minimization
constants + `delimit_untrusted()`/`UNTRUSTED_SOURCE_NOTICE` — the prompt-injection mitigation: source
content is wrapped in `<source ref="...">` blocks with an explicit "evidence, not instructions"
notice), `research_extraction.py`/`score_explanation.py`/`personalization.py`/`objective_parse.py` —
each with a typed input (`ResearchExtractionInput`/`ScoreExplanationInput`/`PersonalizationInput`/
`ObjectiveParseInput`, all constructed only from a `ProspectContext` or, for `objective_parse`, the raw
objective text), a `PROMPT_VERSION` string, and `build_envelope()`. Token minimization: research
sources bounded to 600 chars/snippet; score explanation sees only the top 3 dimensions, never all
eight; personalization sees at most 4 grounded signals. `engine/steps/{research,score,personalize}.py`
now build envelopes via these prompt modules instead of constructing `PromptEnvelope` inline; metadata
is still populated identically so `DemoLLMProvider` remains byte-identical.
`models/llm_io.py::ObjectiveParseOutput` added (criteria-only — deliberately never asked to echo
`objective_text`/`target_count`).

**Phase 3 — LLM call persistence** (`llm_calls` table, additive; `run_events` untouched):
`models/tables.py::LLMCallRow` — one row per provider *attempt*, `UNIQUE(call_group_id, attempt)`.
`objective_parse` rows set `play_id`, leave `run_id`/`prospect_id` null; pipeline-operation rows set
`run_id`/`prospect_id`/`step_name`, leave `play_id` null. `repositories/llm_calls.py::LLMCallRepository`
— `record_attempts()` (the hot path) and `create_play_with_attempts()` (Phase 9's one-transaction
Play+telemetry write). `observability/llm_calls.py::LLMCallRecorder` — pre-bound to
`(run_id, prospect_id)` like `TraceRecorder`/`EventEmitter`; catches and logs persistence failures
rather than letting them fail a successful model operation (`engine/llm.py`'s docstring names this
explicitly). `engine/llm.py::call_structured()` — the CRITICAL BOUNDARY: the only thing that persists
attempt telemetry; `providers/*` never import a repository or SQLAlchemy (enforced by
`tests/test_provider_purity.py`, static source inspection). Also rolls model/provider/token totals onto
`ctx.llm_rollup[step_name]`, which `engine/step.py`'s OK branch folds into that step's `agent_tasks`
row — confirmed live: the TraceTable's Provider/model column now shows `demo_llm · demo-llm-v1` for
every LLM-driven step (see the Prospect Detail screenshot). `DemoLLMProvider` produces exactly one
INITIAL/OK attempt per logical call. `evaluation/metrics.py` gained `llm_usage` (Phase 8, see below).

**Phase 4 — OpenAI SDK / strict schema / output cap spike**: pinned `openai==3.6.0` (current major at
build time; this environment's `httpx` dependency, notably, has a `httpx2` major-version successor that
`openai` 3.6.0 depends on directly — the SDK and the rest of this repo import two *different* httpx
packages, confirmed by inspecting the installed package, not assumed). Verified against the actually
installed SDK (`site-packages/openai/types/responses/response.py` et al.), not stale plan pseudocode:
Responses API request via `client.responses.create(model=, input=[...], reasoning={"effort": ...} |
omitted, text={"format": {...}}, max_output_tokens=, store=False, timeout=)`; response fields
`.status` (`completed`/`failed`/`incomplete`/...), `.incomplete_details.reason`
(`max_output_tokens`/`content_filter`), `.output` (message items with `.content` of
`output_text`/`refusal` blocks), `.output_text` convenience, `.usage.{input_tokens,output_tokens,
output_tokens_details.reasoning_tokens,total_tokens}`, `.id` (provider request id), `.error`
(`ResponseError{code, message}`). Exceptions: `AuthenticationError`(401) < `RateLimitError`(429) <
`APITimeoutError` < `APIConnectionError` < `APIStatusError`(everything else incl. 5xx as
`InternalServerError`) — caught most-specific-first. `max_retries=0` set on `AsyncOpenAI` construction.

`providers/live/schemas.py::to_strict_json_schema()` — mechanically tightens Pydantic's own
`model_json_schema()` output: every object node gets `additionalProperties: false` and **every**
property forced into `required` (optionality is expressed by the property's own nullable type, which
Pydantic already generates correctly for `Optional[X] = None` fields — this is a schema *view*
transform, never a domain-model weakening). `is_strict_compatible()` walks the result and flags
violations; `tests/test_strict_schema_compat.py` runs it over all four operations.

**Output cap measurement** (the actual numbers, not guesses): serialized worst-case padded instances of
all four output schemas (fixture-derived facts, `'x' * N` padding for text fields) —
`research_extraction` (largest, across every fixture company): **3,048 chars**; `objective_parse`:
1,427 chars; `personalization`: 2,746 chars; `score_explanation`: 519 chars. At a conservative
~3.5 chars/token, the worst case is **~871 visible tokens**. `LLM_MAX_OUTPUT_TOKENS=2048` selected —
comfortably >1.5× that worst case (headroom for real-model verbosity above the synthetic padding, plus
low-effort reasoning tokens, which count toward the same budget) while staying <6× it (not preserving
the plan's provisional 3000 "merely because the plan mentioned it" — regression-tested by
`tests/test_output_cap_sizing.py`, which fails if the configured cap drifts outside that band).

**Phase 5 — process-scoped live runtime** (`providers/live/runtime.py::LiveProviderRuntime`):
`AsyncOpenAI` client + `asyncio.Semaphore(LLM_MAX_CONCURRENCY)` + resolved model/reasoning/pricing,
created ONCE in `main.py`'s `lifespan` (only when `OPENAI_API_KEY` is configured — a public clone with
no key never touches live-provider machinery) and closed ONCE at shutdown. `api/deps.py::get_live_runtime`
reads it off `request.app.state.live_runtime`; tests override the FastAPI dependency, or construct a
`LiveProviderRuntime` directly with `http_client=httpx2.AsyncClient(transport=ScriptedTransport(...))`
(`tests/live_helpers.py`) — no automated test ever makes a real network call.
`tests/test_run_budget_and_runtime.py::test_process_scoped_semaphore_bounds_concurrent_calls` proves two
independent `OpenAILLMProvider`s referencing the same runtime (standing in for two simultaneous runs)
genuinely serialize through one semaphore.

**Phase 6 — live OpenAI provider** (`providers/live/openai_llm.py::OpenAILLMProvider`): the flat retry
loop — ONE `while True` loop, counters (`transport_retries_consumed`, `schema_round`,
`schema_repair_used`, `flat_attempt`) initialized once, ONE outbound-request call site (`_issue()`).
`transport_retry_index` is a single counter for the whole call that is never reset by a schema-repair
attempt; `schema_round` flips 0→1 exactly once, the moment the one allowed repair attempt is issued,
and stays 1 for any transport retries after that. Max attempts with `T=2, S=1` is `1+T+S=4`, verified
never `(1+T)*(1+S)=6` by both deterministic worked-sequence tests and a 60-iteration randomized property
test (`tests/test_live_openai_provider.py::test_property_attempt_count_never_exceeds_budget`).
Response classification: `TRUNCATED` (permanent, even with empty visible output, per spec) and
`REFUSED`/`CONTENT_FILTERED`/`AUTH_ERROR` (permanent) raise immediately with zero retry/repair;
`INVALID_JSON`/`SCHEMA_MISMATCH`/genuine `NO_OUTPUT` get exactly one schema-repair attempt (a follow-up
user turn naming the validation error and the previous output, asking for schema-conformant JSON only);
`TIMEOUT`/`RATE_LIMITED`/5xx `PROVIDER_ERROR` consume the shared transport budget. Every attempt is
gated through `runtime.semaphore` individually (not once per logical call), so
`LLM_MAX_CONCURRENCY` bounds real concurrent HTTP requests, not logical calls.

**Phase 7 — cost control / config / registry**: `config.py` gained
`LIVE_MAX_PROSPECTS_PER_RUN=5`, `LLM_MAX_CONCURRENCY=2`, `LLM_MAX_TRANSPORT_RETRIES=2`,
`LLM_MAX_SCHEMA_RETRIES=1`, `LIVE_STEP_TIMEOUT_S=45`, `LIVE_RUN_WALL_CLOCK_TIMEOUT_S=600`,
`LLM_CALL_DEADLINE_S=30`, `LLM_MAX_OUTPUT_TOKENS=2048`, `OPENAI_MODEL=gpt-5.6-terra` (config-only —
no application code branches on this string; `gpt-5.6-luna` is the named lower-cost profile),
`OPENAI_REASONING_EFFORT=low` (empty string omits the `reasoning` field from the request entirely —
verified by `tests/test_live_openai_provider.py::test_reasoning_effort_omitted_when_empty`),
`OPENAI_PRICE_{INPUT,OUTPUT}_USD_PER_MTOK` (unset → `cost_usd` stays `null` everywhere, never guessed),
`LIVE_RUN_SOFT_BUDGET_USD` (soft, never a hard cap). `engine/run_budget.py::RunBudget` — lock-protected
async accounting; gate-before-call, charge-after-completion; `is_tripped()`/`charge()` are the only
mutating operations, both behind an `asyncio.Lock`, verified race-safe under 10 concurrent coroutines ×
200 charges each with zero lost updates. A blocked call never makes an HTTP request — it synthesizes
one `NOT_ATTEMPTED_BUDGET` telemetry row and raises `ProviderBudgetExceeded` immediately.
`providers/registry.py::build_provider_bundle(mode, ..., live_runtime=None, run_budget=None)` — Live
without a configured runtime raises `ProviderNotConfigured`, **never** a silent `DemoLLMProvider`
fallback; the live branch imports `providers/live/openai_llm` lazily (inside the function), so the
`openai` SDK is never even imported on a pure-Demo-Mode request path.
`providers/profile.py::build_provider_profile()` — the truthful, no-secrets snapshot (LLM
provider/model/reasoning effort, prompt versions, search provider, `synthetic_search: true`,
evidence origin, output/call/prospect bounds, soft budget + its enforceability, `deterministic: false`
for Live / `true` for Demo) persisted onto `RunRow.provider_profile` at run creation.

**Phase 8 — API**: `PlayCreateRequest.mode`/`RunCreateRequest.mode` widened to `Literal["demo",
"live"]` (was `Literal["demo"]` — the old `test_create_play_rejects_live_mode` test is now
`test_start_run_rejects_live_mode_without_configured_runtime`, since Live Mode is real). `PlayResponse`
gained `parse_source: "llm" | "deterministic"`; `RunResponse` gained `provider_profile`.
`GET /settings/providers` extended with a `live: LiveAvailability` block (availability computed from
the real runtime, never assumed; model, reasoning effort, prompt versions, search provider, hard
bounds, `pricing_configured`, soft budget + enforceability — no secret values). `evaluation/metrics.py`
gained `llm_usage` (computed on read from `llm_calls`): logical call count (distinct `call_group_id`),
provider-attempt count, token totals, reasoning tokens (null if none exposed), `estimated_cost_usd`
(null unless *every* contributing attempt has a non-null cost — never a partial sum presented as
complete), per-operation and per-status breakdowns, transport-retry/schema-repair counts, and whether
the run's soft budget tripped. No token/cost frames were added to `run_events` (stays the resumable SSE
progress log, per the invariant).

**Phase 9 — objective parser** (`engine/objective_parser.py::parse_objective()`): runs before any
`Play` row exists — zero DB writes, at most one LLM call, attempt telemetry held in memory. On ANY
`ProviderError` (refusal/truncation/schema exhaustion/timeout/budget/etc.) it falls back
deterministically to the exact construction Demo Mode has always used — never an exception, never a
500. `ObjectiveParseOutput` is criteria-only (no `objective_text`/`target_count` echo — enforced by a
schema-shape test). User `icp_overrides` are applied **after** LLM inference, so they always win.
`api/routers/plays.py::create_play` is the only caller: it invokes `parse_objective()`, then creates the
`Play` row and its `objective_parse` `llm_calls` rows in **one transaction**
(`LLMCallRepository.create_play_with_attempts`) — an `llm_calls` row can never reference a nonexistent
`Play`, and a failed transaction rolls both back together (verified by
`tests/test_objective_parser.py::test_play_and_llm_calls_created_in_one_transaction`, which runs the
real `OpenAILLMProvider` against a scripted transport end-to-end into real DB rows).
`PlayCreateRequest.use_live_objective_parser: bool = False` — the explicit, deliberate flag; the New
Play form's 600ms debounce never sets it (see Phase 10), so there is no paid-request-per-keystroke path.

**Phase 10 — frontend** (minimal, no redesign): `app/plays/new/page.tsx` — Demo/Live segmented toggle
(Live disabled + explained when `!live.available`, confirmed live via the real UI: see screenshots);
selecting Live shows the model, reasoning effort, `LIVE LLM · FIXTURE SEARCH` explanation, capped
prospect count, the hard worst-case attempt/token bound computed from `GET /settings/providers` (never
hardcoded), the soft budget only when `soft_budget_enforceable`, and an explicit pre-spend confirmation
checkbox gating Run Agents. A separate **"Parse with model"** button is the only path that ever sets
`use_live_objective_parser: true` — the 600ms debounce still renders a deterministic preview immediately
in Live Mode (free, no LLM call) but never triggers the live parser. `parsedPlay.parse_source` renders
next to the parsed spec when in Live mode. `components/RunSummary.tsx` — the run-mode badge now reads
`LIVE LLM · FIXTURE SEARCH · <model>` for live runs (was a bare `LIVE`), `DEMO` unchanged.
`components/ModelUsagePanel.tsx` (new) — Model Usage & Cost on the Quality tab, backed only by
`/evaluation`'s `llm_usage`; renders `—` for null tokens/cost rather than fabricating. `TraceTable`
required zero changes — it already renders `agent_tasks.model`/`.provider`, which Phase 3's rollup now
actually populates.

**Phase 11 — security**: `observability/redact.py::redact()` — the central choke point every
error-to-string path routes through before persistence (`LLMCallRepository`'s row-builder calls it on
both `error_message` and `validation_error`). Strips any configured `OPENAI_API_KEY`/`TAVILY_API_KEY`
value plus any generic `sk-...`/`Bearer ...`-shaped token, and truncates long payloads. Verified with a
sentinel secret deliberately echoed by a fake provider error, end-to-end through
`LLMCallRepository.record_attempts()` into a real DB row (`tests/test_redaction.py`) — the sentinel
appears nowhere in the persisted row. `.env.example` documents every new setting with empty secrets;
Demo Mode's tests explicitly monkeypatch `openai_api_key = None` and still fully complete.

`scripts/live_smoke.py` (new, `make live-smoke`) — the OPTIONAL real-live smoke test. Requires
`--i-understand-this-costs-money` AND a configured key; runs exactly one fixture prospect through the
real API; prints configured model/effort/hard bounds (and a dollar bound only if pricing is
configured) before the request, full per-attempt telemetry/tokens/cost/score/outreach/verdict after;
exits nonzero if any attempt was `TRUNCATED`. **Not run this session** — no `OPENAI_API_KEY` was
provided and none was requested from the user, per the task's own instruction to run it "only if
credentials are available and I explicitly provide/approve them."

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

**Checkpoint D verification** — no frontend automated test suite was added (not in scope for this
checkpoint); verified instead by build/lint gates plus headless-Chromium browser walkthroughs against
the real running stack (`uv run uvicorn ... --port 8010` + `pnpm dev --port 3000`,
`NEXT_PUBLIC_API_URL` pointed at 8010; port 3000 required to match `cors_origins` in `config.py` —
running the web app on a different port 400s on CORS preflight, confirmed the hard way first).

1. `cd apps/api && uv run pytest` — **63/63 still passing**, unchanged, confirming zero backend
   regressions from this checkpoint (`git status` shows no `apps/api/**` diff).
2. `cd apps/web && pnpm lint` — clean. `pnpm build` — compiles, typechecks, and prerenders `/` and
   `/plays/new` static, `/runs/[id]` and `/prospects/[id]` dynamic, with no errors.
3. Browser: filled New Play with the plan's own demo objective, watched the debounced `PlanPanel`
   populate with real parsed criteria (industries chip, size band, score/confidence/count) from the
   live `POST /api/plays` response. Clicked **Run Agents** → navigated to `/runs/{run_id}` immediately
   on the 202, before the run had produced any prospects yet.
4. Polled fresh page loads of the same run at ~150ms/400ms/700ms/2500ms after creation (simulating
   repeated refreshes at different points in a run that completes in under 2s in Demo Mode): at 150ms,
   two rows showed `queued` stage with a live retry badge (`research · retry 1`, `research · retry 2`)
   while others had already reached terminal status — direct visual proof of bounded, independently-
   paced concurrency and mid-pipeline retry visibility. By 700ms the run had reached `PARTIAL` with all
   six rows terminal (`PASS` ×2, `NEEDS_REVIEW` ×2, `DUPLICATE` ×1, `FAILED` ×1) and stayed
   byte-for-byte identical (no backward jumps, no duplicate rows beyond the intentional
   `DUPLICATE`-status row) across every later reload through 2500ms.
5. Reconnect resilience: routed `**/api/runs/*/events*` through Playwright to abort every request for
   2.5s after page load, confirming the client's manual backoff loop kept retrying (4 attempts
   observed, connection chip showing `reconnecting…`) rather than giving up; unblocked the route and
   confirmed the client reopened the stream, the connection chip returned to a closed/live state
   appropriately, and the board reconciled to the exact same fully-correct final state as an unblocked
   run — proving the debounced `GET /runs/{id}/prospects` reconcile-on-reconnect path is what actually
   repairs state, not the SSE frames alone.
6. Direct nav to a nonexistent run id (`/runs/does-not-exist`) renders the friendly "Run … could not be
   loaded" error state (RFC-7807 detail shown as secondary mono text, not a raw stack trace); direct nav
   to `/prospects/{id}` renders this checkpoint's placeholder (Checkpoint E's real content) without a
   broken-navigation dead end.
7. Viewport check at 1440×900 (primary) and 1280×800: no horizontal overflow on either the board table
   (`overflow-x-auto` wrapper) or the page body; `document.documentElement.scrollWidth <=
   clientWidth` confirmed via `page.evaluate` at 1280px.
8. Row navigation: clicking a `ProspectRow` calls `router.push('/prospects/{id}')` with the real
   `ProspectSummary.id` from the reconciled board state, not a client-generated or stale id.

---

**Checkpoint E verification** — no new backend tests (no backend files changed); no new frontend
automated tests (matches Checkpoint D's precedent — `apps/web/package.json` still has no test runner
configured). Verified by build/lint gates plus headless-Chromium (`playwright`, pre-installed in this
environment) walkthroughs against the real running stack (`uv run uvicorn ... --port 8010` +
`NEXT_PUBLIC_API_URL=http://localhost:8010 pnpm dev --port 3000`).

1. `cd apps/api && uv run pytest` — **63/63 still passing**, confirming zero backend regressions
   (`git status`/`git diff --stat` show no `apps/api/**` diff for this checkpoint).
2. `cd apps/web && pnpm lint` — clean. `pnpm build` — compiles, typechecks, prerenders
   `/`/`/plays/new` static, `/prospects/[id]`/`/runs/[id]` dynamic, no errors.
3. Ran a real Demo Mode play + run against the live API (`POST /api/plays` with the fixture pack's own
   ICP overrides, `POST /api/plays/{id}/runs`) → reached `PARTIAL` with the fixture's documented spread
   (`PASS:2 NEEDS_REVIEW:2 DUPLICATE:1 REJECTED:1 FAILED:1`).
4. Navigated Run Detail → clicked the Northwind Labs row → landed on
   `/prospects/{northwind_id}`, confirming Checkpoint D's board-row navigation target (unmodified) is
   now real content instead of the placeholder.
5. **Score reconciliation, read off the rendered page, not asserted**: Northwind Labs — 8 dimension
   contributions (`+20.0 +15.0 +10.7 +13.8 +10.0 +10.0 +8.5 +4.4`) sum to `+92.4` → rounds to **92**,
   matching the displayed ICP score of **92**. Ferrous Grid (`NEEDS_REVIEW`, three dimensions
   `unsupported`) — contributions sum to **58**, matching. Cobalt Retail Systems (`REJECTED`, hard
   disqualifier on `retail_pos`) — rubric total is **69** but the displayed score is **25**; the
   modifiers panel shows `hard_disqualifier — industry 'retail_pos' is on the exclude list` with
   `overall capped from 69 to 25`, and the reconciliation line correctly switches to `rubric total 69
   → capped to 25 by the modifier above` instead of asserting the two numbers match — confirmed
   directly via the API response (`modifiers[0].detail == "overall capped from 69 to 25"`), not just
   visually.
6. Evidence provenance, verified per item: every Northwind/Riverbend/Ferrous Grid evidence card shows
   "Synthetic evidence · demo fixture" with **no** `<a href>` present in the rendered DOM for any
   `DEMO_FIXTURE` row (checked via Playwright's rendered output, not just the source) — the origin
   check in `EvidenceCard.tsx` gates on `origin === "LIVE_FETCH" && source_url`, so no fixture row can
   ever produce a link regardless of what's in `source_url` (which is `null` for every fixture row
   per the backend's own `_no_fake_sources` validator).
7. Outreach + review use actual persisted records: Northwind Labs' draft body cites
   "Northwind Labs raised a $42M Series B" and "...GTM hiring surge", both resolving through
   `claim_map` to real evidence titles; its Review panel shows all seven checks passing. Riverbend
   Analytics' Review panel shows `score_support` and `confidence_floor` both `FAIL` (soft), consistent
   with its `NEEDS_REVIEW` verdict and its demoted funding signal (rendered with an "ungrounded —
   demoted, does not score" badge in Signals).
8. Approve/reject, exercised live end-to-end (not simulated): approved Northwind Labs
   (`POST /prospects/{id}/approve`) — page updated to `Decision APPROVED · by demo_user · <timestamp>`,
   Approve/Reject buttons disappeared; confirmed via a **separate** `curl
   /api/prospects/{id}` that `approval.state == "APPROVED"` and the engine-owned `status` column was
   still `PASS` (untouched, per the Checkpoint C invariant). Rejected Riverbend Analytics with a typed
   reason — same pattern, `approval.reason` persisted verbatim. Reloaded both pages fresh: decisions
   persisted (read from the backend, not local state). Confirmed `DUPLICATE`/`FAILED` prospects render
   **no** Approve/Reject controls at all (client-side mirrors the backend's `_DECIDABLE_STATUSES` 409
   gate rather than only reacting to a failed request).
9. Retry attempts render as independent rows: Northwind Labs' trace shows
   `research · attempt 1 · RETRY · ProviderTimeout: scripted ProviderTimeout for northwind-labs/rese…`
   immediately followed by `research · attempt 2 · OK` — exactly the sequence the task calls out.
   Quarry Systems (`FAILED`) shows all three exhausted attempts (`RETRY`, `RETRY`, `FAILED`), each with
   its own `ProviderUnavailable` message, none collapsed into a single line.
10. Opened all required degraded states and confirmed graceful rendering, no crash, no broken-looking
    layout: `NEEDS_REVIEW` (Riverbend, Ferrous Grid), `DUPLICATE` (Northwind Labs Inc. — empty
    Score/Evidence/Signals/Contact/Outreach/Review panels each show an explicit "did not run" /
    "stopped before" message, plus a link to the earlier prospect it collided with), `FAILED` (Quarry
    Systems — same degraded-panel treatment, plus the pipeline error surfaced in the header),
    `UNAVAILABLE` contact (Ferrous Grid — explicit "intentional outcome, not a missing field" copy,
    not blank space).
11. Viewport check at 1440×900 on all seven prospects in this run:
    `document.documentElement.scrollWidth === document.documentElement.clientWidth` (1440 vs 1440,
    zero overflow) on every one, verified via Playwright, not just visually.
12. No browser console errors or page errors across any of the pages visited during this walkthrough
    (approve/reject flows included).

Two screenshots were captured during this walkthrough (PASS full detail — Northwind Labs; FAILED
detail — Quarry Systems) and shown to the user in-session. Per instructions, they were not committed
to the repository.

---

**Checkpoint F verification.**

1. `cd apps/api && uv run pytest` — **63/63 passing** both before and after every change this
   checkpoint made (the `metrics.py` addition, the `target_count` default change) — zero regressions,
   confirmed by re-running the full suite after each edit, not just at the end.
2. `cd apps/web && pnpm lint` — clean. `pnpm build` — compiles, typechecks, prerenders `/`, `/plays/new`
   static and `/icon.svg`, `/prospects/[id]`, `/runs/[id]` dynamic, no errors — checked after every
   round of frontend edits in this checkpoint, not just once at the end.
3. `make demo-reset && make demo`, run twice independently: **identical** status distribution both
   times (`PASS: 2, NEEDS_REVIEW: 2, REJECTED: 1, DUPLICATE: 1, FAILED: 1`, 3 retries recorded),
   matching this file's own Checkpoint B reference numbers exactly.
4. **Two full clean-reset browser rehearsals** (`make demo-reset` between them, API and web servers
   both restarted, Playwright/Chromium driving the real running stack — not curl, not a mock) of the
   full canonical founder demo: New Play → Run Agents → concurrency → mid-run refresh → terminal state →
   Quality tab → open the PASS prospect → approve → reject another prospect → open the FAILED prospect →
   open the DUPLICATE prospect → invalid run URL → invalid prospect URL. Both rehearsals reproduced the
   identical outcome distribution and identical per-company scores (see the demo-consistency bug section
   above — the *first* rehearsal pass is what surfaced that bug; the two passes reported here are the
   two *post-fix* confirmations). Zero page-level JavaScript errors either time; the only console
   `404`s were the intentionally-invalid-URL fetches (2 per pass) plus, before the favicon was added,
   one `/favicon.ico` 404 per page navigation — gone after the fix. Zero horizontal overflow at
   1440×900 on the run board, prospect detail, or Quality tab.
5. Quality-tab numbers reconciled by hand against the board on both rehearsals: Volume
   (discovered/completed/PASS/NEEDS_REVIEW/REJECTED/DUPLICATE/FAILED = 7/7/2/2/1/1/1) matches the board
   exactly; guardrail pass rates (`score_support` 3/5, `confidence_floor` 4/5, all others 5/5) match the
   two `NEEDS_REVIEW` prospects' individually-rendered Review panels; evidence coverage 67% = 4 of 6
   non-duplicate researched prospects with ≥3 evidence rows (Quarry never reached Research).
6. `make demo-reset && make dev` from the current branch — the full §32 14-item checklist walked
   manually via the real UI: create play (parsed spec visible) → Run Agents → concurrent rows → retry
   badge visible → PASS/NEEDS_REVIEW/REJECTED/DUPLICATE/FAILED all present → open Northwind Labs →
   evidence with provenance chips (SYNTHETIC badge, no link) → score breakdown table reconciling to 92 →
   outreach citing grounded signals → all seven review checks → approve (and reject Riverbend
   Analytics separately) → execution trace with a visible retry → Quality tab with computed metrics and
   guardrail pass rates → scaling story is now written into `README.md` and `DEMO_SCRIPT.md` beat 13.
   All 14 pass.
7. A ~90-second fallback screen recording (Playwright's built-in video capture, `.webm`) of the
   canonical flow — New Play → Run Agents → concurrency hold → Northwind Labs score breakdown →
   evidence → outreach → review → approve → Quality tab — was captured and sent to the user for local
   review. Per instructions, not committed to the repository.

---

**Checkpoint G verification.**

1. **Baseline first** (Phase 0): `uv run pytest` — 63/63 — and `make demo-reset && make demo` recorded
   verbatim to a scratch file before any change, matching this file's documented reference numbers
   exactly (Northwind Labs 92, Riverbend Analytics 35, Cobalt Retail Systems 25, Ferrous Grid 58, Sable
   Compute 79; `PASS:2 NEEDS_REVIEW:2 REJECTED:1 DUPLICATE:1 FAILED:1`, 3 retries, `run status: PARTIAL`).
2. `cd apps/api && uv run pytest` — **114/114 passing** (63 original + 51 new: 20
   `test_live_openai_provider.py`, 9 `test_strict_schema_compat.py`, 1 `test_output_cap_sizing.py`, 1
   `test_provider_purity.py`, 5 `test_redaction.py`, 6 `test_objective_parser.py`, 6
   `test_run_budget_and_runtime.py`, 1 `test_live_pipeline_integration.py`, 1
   `test_demo_llm_calls_additive.py`, plus a net +1 in `test_api_plays_runs.py` — a stale
   Live-Mode-is-rejected test replaced with two that reflect Live Mode now being real). Re-run after
   every phase gate, not just once at the end — zero regressions the whole way.
3. `make demo-reset && make demo` re-run after every phase — **byte-identical to the Phase 0 baseline
   at every gate**, confirmed both by direct text diff of the reference numbers and, separately, through
   the real browser UI (see screenshot below): Northwind Labs 92, Riverbend Analytics 35, Cobalt Retail
   Systems 25, Ferrous Grid 58, Sable Compute 79, `PASS:2 NEEDS_REVIEW:2 REJECTED:1 DUPLICATE:1
   FAILED:1`.
4. `llm_calls` additive persistence confirmed directly against a real headless demo run's SQLite file
   (`SELECT operation, provider, model, status, attempt, step_name FROM llm_calls`): 14 rows, one
   `INITIAL`/`OK` attempt per logical call, `provider=demo_llm`, `model=demo-llm-v1` — and the
   `agent_tasks` rollup confirmed on the same run (`SELECT step_name, model, provider, tokens_in,
   tokens_out FROM agent_tasks WHERE model IS NOT NULL`), non-null for every research/score/personalize
   row.
5. **Fake-Live end-to-end** (`tests/test_live_pipeline_integration.py`): one prospect through the REAL
   `execute_run`, REAL rendered prompts, and a fake Responses transport — completed to a terminal
   status, exactly 2 `llm_calls` rows (research + score; personalize skipped, no contact — the
   sable-compute fixture has no leadership fact), both `provider=openai`/`status=OK`, evidence still
   100% `DEMO_FIXTURE`/`source_url=None` (confirms search stays fixture-backed in Live Mode).
6. Retry/schema/refusal/truncation/content-filter/auth/rate-limit/5xx behavior — all individually
   proven in `tests/test_live_openai_provider.py` against a scripted `httpx2.MockTransport`, plus the
   flat-retry-loop invariants specifically: `test_transport_budget_never_resets_after_schema_repair`
   (a hand-worked 4-attempt sequence: timeout, timeout, invalid_json→schedules repair, repair itself
   times out — budget already exhausted, raised immediately, never a 5th attempt) and
   `test_property_attempt_count_never_exceeds_budget` (60 randomized failure sequences up to length 7,
   every one resolving in 1–4 attempts, `transport_retry_index` monotonic and capped at `T=2`, at most
   one `schema_repair` attempt ever).
7. `cd apps/web && pnpm install && pnpm lint` — clean (one real `react-hooks/set-state-in-effect`
   violation was hit and fixed during this checkpoint: an effect that reset `mode` back to `"demo"` when
   Live became unavailable was replaced with a derived `effectiveMode` value, since the Live toggle
   button is already disabled in that state and nothing needs syncing). `pnpm build` — compiles,
   typechecks, prerenders `/`/`/plays/new` static and `/prospects/[id]`/`/runs/[id]` dynamic, no errors.
8. **Real browser walkthrough** (`make demo-reset`, API on `:8010`, web on `:3000`, headless Chromium
   via Playwright — not curl): New Play renders the Demo/Live toggle with Live correctly disabled and
   its explanation shown (no `OPENAI_API_KEY` configured in this environment); clicked Run Agents in
   Demo Mode; the run reached `PARTIAL` with the exact canonical distribution and per-company scores,
   confirmed on-screen; Quality tab's new Model Usage & Cost panel rendered real data (14 logical
   calls, 14 provider attempts, 919 tokens in / 834 out, reasoning tokens `—`, estimated cost `—` — both
   correctly null since Demo Mode's provider never sets them); Prospect Detail's execution trace now
   shows `demo_llm · demo-llm-v1` in the Provider/model column for every LLM-driven step attempt (was
   blank before this checkpoint). Zero browser console/page errors across the whole walkthrough.
   Screenshots sent to the user in-session (New Play Demo, Run Detail board, Quality tab, Prospect
   Detail); per instructions, not committed to the repository.
9. **Live smoke test: not run in this session.** No `OPENAI_API_KEY` was provided or requested from the
   user this session, and the task instructions were explicit that it only runs "if credentials are
   available and I explicitly provide/approve them." `scripts/live_smoke.py` was manually verified to
   refuse safely without the flag and without a configured key (both checked, both correctly exit 1
   with no network call attempted). **The user ran it for real after PR #7 was opened** — see
   "Post-smoke hardening pass" immediately below for the result and what it surfaced.

---

## Post-smoke hardening pass (after PR #7, before merge)

The user ran `make live-smoke` for real, against the actual OpenAI API, after PR #7 was opened. It
succeeded — the first genuinely real execution of Live Mode. This section records the result and the
four issues that first real run surfaced, all fixed in the same PR before merge (not a new checkpoint —
Checkpoint G was not redesigned, only hardened).

**The successful real run:**

- Model `gpt-5.6-terra`, `reasoning_effort=low`, `llm_max_output_tokens=2048`, pricing intentionally
  left unconfigured (so cost stayed `null`, as designed).
- Three real logical LLM calls (research_extraction, score_explanation, personalization — objective
  parse was not yet wired into `live_smoke.py` at the time of this run; see Issue 4 below), every one
  `attempt=1, kind=initial, status=OK` — **zero transport retries, zero schema repairs, zero TRUNCATED
  responses** across the whole run. Total tokens: 1,889 in / 699 out.
- Final prospect (Sable Compute): `score=56`, `status=REJECTED`, `review verdict=FAIL`,
  `claim_map entries=3`.

This is a genuinely clean real-provider result on the success axis: the strict Structured Outputs
schema validated on the first attempt for all three operations, at real API latency, with a real model.
The `REJECTED` outcome is analyzed separately below (Issue 3) — it is not a failure of the smoke test
itself.

### Issue 1 — blank optional `.env` floats crashed `Settings()`

**Root cause, confirmed by reproduction:** `.env.example` documents `OPENAI_PRICE_INPUT_USD_PER_MTOK=`,
`OPENAI_PRICE_OUTPUT_USD_PER_MTOK=`, and `LIVE_RUN_SOFT_BUDGET_USD=` as blank-by-default (`float | None`
fields, intentionally optional per §7). Pydantic's own float parsing rejects an empty string outright —
`Settings()` raised a 3-field `ValidationError` on construction for anyone who copied `.env.example` to
`.env` verbatim, before the API could even start. Reproduced directly:
`OPENAI_PRICE_INPUT_USD_PER_MTOK="" uv run python -c "from groundwork.config import Settings; Settings()"`
raised `pydantic_core._pydantic_core.ValidationError: ... Input should be a valid number ... input_value=''`.

**Fix:** `config.py::Settings` gained a `field_validator(mode="before")` on all three fields that
normalizes a blank/whitespace-only string to `None` before Pydantic's type coercion runs — the
documented "unset -> `None` -> cost stays null / threshold unenforceable" semantics now hold for a
blank string exactly like a genuinely absent env var. No typing was weakened — the fields are still
`float | None`; only what counts as "absent" widened from "key not present" to "key present but blank."
`tests/test_settings_blank_env.py` (6 tests): blank strings, whitespace-only strings, real numeric
values, and the key-absent-entirely case all produce the documented semantics.

### Issue 2 — a pre-Checkpoint-G local SQLite DB crashed with a raw stack trace

**Root cause:** Checkpoint G added `runs.provider_profile` (a column) and `llm_calls` (a table).
`create_all()` only creates tables that don't exist yet — it never alters an existing table to add a
new column — so a developer's local `groundwork.db` predating this checkpoint hit
`sqlite3.OperationalError: table runs has no column named provider_profile` on the first write, with no
explanation of what to do about it. `uv run python -m groundwork.scripts.reset` fixed it, but nothing
told the user that was the fix.

**Fix (no Alembic/Postgres — explicitly out of scope):**
- `db.py::schema_upgrade_problems(engine)` (new, read-only, never mutates or resets anything) inspects
  the live DB for exactly the two things this checkpoint added and returns a human-readable problem
  list — empty for a current or brand-new (no tables yet) database.
- `scripts/live_smoke.py` calls it immediately after `create_all()`, **before constructing
  `LiveProviderRuntime` or making any paid call**, and aborts with
  `"Local Groundwork DB predates Checkpoint G: ... Run make demo-reset ... Aborting BEFORE making any
  paid API call — your existing local data was not touched."` if it finds a problem.
- `README.md` gained an explicit "Upgrading an existing local checkout past Checkpoint G" section
  documenting the one-time `make demo-reset` requirement.
- `tests/test_schema_upgrade_check.py` (4 tests): a hand-built pre-Checkpoint-G schema is correctly
  flagged on both counts; the current full schema (via the existing `session_factory` fixture) reports
  no problems; a brand-new empty database reports no problems (not "stale," just uninitialized); the
  check function never mutates the database it inspects.

### Issue 3 — why Sable Compute landed `REJECTED`/`review verdict=FAIL`

**What this section can and can't claim:** the actual run's `review_results`/`llm_calls` rows live in
the user's own local SQLite file from their own environment, not in this session — there is no database
here to query directly. What follows is code-level analysis plus a reproducible demonstration against
the real, unmodified `token_overlap` function and the real Sable Compute fixture text; it is the
best-supported explanation available without that row, not a claim of having read it.

**Ruled out mechanically, not by guessing:** `score=56` with `review verdict=FAIL` means
`_derive_final_status` took the review-FAIL branch, not the hard-disqualifier branch (that requires
`score.disqualified`, which caps the score at 25 the way Cobalt Retail Systems' fixture does — 56 isn't
a capped number, and Sable Compute isn't on any exclude list). With `target_count=1`, the single
prospect has no siblings in the run — `other_dedupe_keys`/`other_company_identifiers` are both empty
sets — so `cross_prospect_leak` and `duplicate_account` (both membership checks against those sets)
cannot fail. `no_fabricated_contact` only fails if `contact.email`/`contact.linkedin_url` is set without
`VERIFIED`; nothing in `engine/steps/enrich.py::resolve_contact` ever sets either field anywhere in this
codebase, so that check cannot fail either, live or demo. That leaves exactly two candidates:
`claim_grounding` and `no_placeholders`.

**Most likely: `claim_grounding`.** `domain/review.py::_claim_grounding` re-verifies each
`claim_map` sentence's token overlap against the ORIGINAL evidence snippet (`domain/grounding.py`,
threshold `0.5`) — not against the intermediate signal summary the personalization model was actually
shown. `prompts/personalization.py` (pre-fix) told the model to cite grounded signals but never told it
this citation would be checked against source wording it never saw, nor asked it to echo that wording —
so a real model, asked to "write a short, personalized outreach email," very plausibly wrote natural
marketing prose instead of a close paraphrase. Demonstrated directly against Sable Compute's actual
fixture snippet ("Sable Compute announced a $20M Series A round to expand its managed training cluster
offering."): a natural-marketing-style sentence ("Congrats on closing your Series A — sounds like an
exciting phase of growth for the team.") scores `token_overlap ≈ 0.10`, while a close-echo sentence
("Congrats on the $20M Series A round for Sable Compute.") scores `≈ 0.83` — a 5–8× gap, comfortably
either side of the `0.5` threshold. `tests/test_personalization_prompt_grounding_alignment.py` makes
this mechanism a permanent, reproducible test.

**This is correct behavior, not a guardrail defect, and the guardrail was NOT weakened.**
`domain/review.py`/`domain/grounding.py` are byte-for-byte unchanged. If the live model in fact wrote a
citation whose wording drifted too far from its source, `claim_grounding` doing exactly its documented
job — catching an insufficiently-grounded claim before it reaches a human reviewer — is the guardrail
working, not failing. That is a real safety demonstration: real-provider prose is measurably harder to
keep tightly grounded than deterministic Demo Mode templating, and the deterministic gate caught it
without needing an LLM to grade itself.

**What was fixed — the actual prompt/wiring gap, not the gate:** `prompts/personalization.py`'s system
prompt (bumped `personalization-v1` -> `personalization-v2`) now explicitly tells the model that each
`claim_map` citation is checked against the original source text and instructs it to echo the cited
signal's key nouns/numbers/phrases rather than paraphrase loosely. This narrows the paraphrase gap at
the one layer that's actually fixable (what the model is told to do) without touching the verification
layer (what counts as grounded) at all.

**Made observable going forward, not just explained once:** `scripts/live_smoke.py` now prints every
one of the seven guardrail checks with its pass/fail state and detail string (`_print_review()`), not
just the verdict — the next real smoke run will show, unambiguously, exactly which check failed and
why, with no inference required.

**Why Sable Compute, not Northwind Labs:** intentional, not a bug and not an accident — `--company`
defaults to `sable-compute` specifically *because* Northwind Labs' fixture carries a scripted research
failure (`fail_attempts=1, error=ProviderTimeout`, used to exercise Demo Mode's retry path). Running
that company live would burn an extra real, billed attempt reproducing a fixture artifact that a real
API essentially never triggers on its own. This was a deliberate choice made when `live_smoke.py` was
first written (see its own module docstring, updated this pass to state the reasoning explicitly), not
a discovery-ranking behavior or a target-count side effect. Per the instruction not to force a PASS
company merely to obtain a nicer smoke result, the default was left as `sable-compute` — `--company
northwind-labs` remains available for anyone who wants to see the retry path exercised against the real
API instead.

### Issue 4 — `objective_parse` wasn't exercised by the smoke

**Confirmed: intentional-by-omission, not a documented design choice.** `live_smoke.py` (as written for
the original PR) called `PlayRepository.create()` directly with the fixture pack's own canonical
`PlaySpec`, bypassing `parse_objective()` entirely — nothing ever claimed this was deliberate coverage
scoping; it was simply not wired in.

**Fixed:** `live_smoke.py` now calls `engine.objective_parser.parse_objective(..., use_llm=True)` as a
real, billed operation before creating the `Play` — the same live-parser path
`POST /plays` takes with `use_live_objective_parser=True` — and persists the `Play` plus its
`objective_parse` telemetry in the same one-transaction call the API uses
(`LLMCallRepository.create_play_with_attempts`). `icp_overrides` passed to `parse_objective()` is
deliberately the fixture's own canonical `PlaySpec` (not empty) — "user overrides always win" means
whatever the model infers, the pipeline still scores against the exact, well-understood fixture
criteria, so the smoke's score/review output stays interpretable regardless of what the live model
happens to infer from the objective text; the real inference call still runs, is still billed, and its
attempts are still fully persisted and printed. This adds exactly one more real logical call (bringing
the smoke to all four Live LLM operations, matching the actual Live Mode user path), not a redundant
one. The preamble's conservative dollar-bound estimate was updated from 3 to 4 operations to match.

**Post-smoke verification:** `cd apps/api && uv run pytest` — **129/129 passing** (114 + 15 new: 6
`test_settings_blank_env.py`, 4 `test_schema_upgrade_check.py`, 5
`test_personalization_prompt_grounding_alignment.py`). `make demo-reset && make demo` re-confirmed
byte-identical to every prior gate (the personalization prompt version bump does not touch
`DemoLLMProvider`, which hardcodes `prompt_version="demo-v1"` regardless of the real prompt's version
string). `cd apps/web && pnpm lint && pnpm build` — clean, unchanged (no frontend files touched this
pass). **Another real live smoke was not run** — nothing in this hardening pass changes the shape of a
live request in a way that needs re-proving against the real API; the fixes are (1) a config-parsing
guard that only affects malformed `.env` values, (2) a pre-flight read-only DB check, (3) a prompt-text
instruction change that the existing per-attempt telemetry printing already proves is wired correctly
end-to-end via the fake-live integration test, and (4) an additional real LLM call whose wiring is
identical to the already-proven per-prospect pipeline calls. The next genuinely necessary real smoke is
whenever the user wants to specifically confirm `claim_grounding` now passes more often with
`personalization-v2`'s wording, or before considering Checkpoint G fully done for a real deployment.

---

## Second post-smoke fix: `create_play_with_attempts` FK-ordering bug

The user re-ran `make live-smoke` after the first hardening pass. **The real `OBJECTIVE_PARSE` LLM call
itself succeeded** (`objective_parse: parse_source=llm, attempts=1`) — the live provider path, the
prompt, and the strict schema all worked correctly against the real API a second time. Persistence then
failed:

```
sqlite3.IntegrityError: FOREIGN KEY constraint failed
[SQL: INSERT INTO llm_calls (...) ]
```

**Root cause, confirmed by direct reproduction (not assumed):** `models/tables.py` defines `LLMCallRow.
play_id` as a raw `ForeignKey("plays.id")` column — and, checked directly, **zero ORM `relationship()`
mappings exist anywhere in that file**; this codebase uses raw FK columns plus manual joins throughout
(consistent with every other repository). SQLAlchemy's unit-of-work only knows to order one table's
INSERT before another's via a `relationship()`-derived dependency processor — a `ForeignKeyConstraint`
on the `Table` alone is not enough to order *DML* (insert order within a flush), only *DDL* (schema
creation order, which is a separate mechanism `create_all()` already uses correctly). `repositories/
llm_calls.py::create_play_with_attempts` was doing `session.add(PlayRow(...))` then
`session.add(LLMCallRow(...))` in a loop, then one `session.commit()` — nothing told SQLAlchemy the
`Play` insert had to happen first, and under real `PRAGMA foreign_keys=ON` it didn't reliably. Reproduced
directly against a real FK-enforced SQLite file with the exact unmodified pre-fix code before touching
anything: `sqlite3.IntegrityError: FOREIGN KEY constraint failed` on the `llm_calls` INSERT, byte-for-byte
the same failure shape the user reported.

**Fix:** one line — `await session.flush()` immediately after `session.add(PlayRow(...))`, before the
`llm_calls` rows are added. `flush()` sends the `Play` INSERT to the database *within the current,
still-open transaction* (not a commit — nothing is durable yet); the final `session.commit()` then makes
both the `Play` and its `llm_calls` rows durable together. Re-reproduced with the fix in place: succeeds,
and a direct row count confirms exactly one `plays` row and one correctly-linked `llm_calls` row.
Rollback was verified too, not assumed: forcing a second `llm_calls` insert to violate
`UNIQUE(call_group_id, attempt)` — which happens in the same flush as the Play was already
flushed-but-not-committed in — leaves **zero** rows in both `plays` and `llm_calls` after the exception,
proving the whole transaction (including the already-flushed Play) rolls back together.

**Why 129 passing tests didn't catch this — a real, now-fixed test-infrastructure gap.**
`tests/conftest.py`'s `_enable_wal` (the connect-event hook every test's `session_factory` fixture uses)
had drifted from `db.py`'s real one: it set `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` but
**never `PRAGMA foreign_keys=ON`** — SQLite does not enforce foreign keys per connection unless that
pragma is set explicitly, connection by connection. Every test in the suite, including
`test_objective_parser.py::test_play_and_llm_calls_created_in_one_transaction` (which does call the real
`create_play_with_attempts`), ran against a database that silently accepted the FK-violating insert
order — the bug was real but structurally invisible to the whole suite. `conftest.py::_enable_wal` now
mirrors `db.py::_enable_wal` exactly (with a docstring recording why this drift mattered, so it isn't
repeated). Re-running the full suite with FK enforcement newly turned on for every test surfaced exactly
one further problem, immediately: `test_redaction.py`'s end-to-end redaction test had been calling
`record_attempts()` with made-up `run_id="run-1"`/`prospect_id="prospect-1"` strings that were never
backed by real rows — it only ever passed because FK enforcement was off. Fixed by creating real
Play/Run/Company/Prospect rows via the actual repositories first, so the test now exercises the real FK
relationship instead of bypassing it.

**New dedicated regression test** (`tests/test_llm_calls_atomicity.py`, 3 tests, real repository, real
FK-enforced SQLite — no mocks): the SUCCESS case (Play + llm_calls both committed, `llm_calls.play_id`
references the real Play, `run_id`/`prospect_id` both `NULL`), the ROLLBACK case (a forced UNIQUE
violation on the second `llm_calls` insert, occurring after the Play's own flush, leaves zero rows in
either table), and a same-process double-call case (two independent `create_play_with_attempts()` calls
don't interfere with each other's Play/llm_calls linkage).

**Confirmed: `POST /plays`'s Live path and `scripts/live_smoke.py` share the same corrected code.** Both
call `repos.llm_calls.create_play_with_attempts(...)` — the single method fixed above. There is no
separate implementation for either caller that could drift from the other.

**No live-provider/request code changed in this fix.** `providers/live/openai_llm.py`,
`providers/live/runtime.py`, `engine/objective_parser.py`, and the request-making parts of
`scripts/live_smoke.py` are untouched — this was purely a persistence-layer bug, confirmed by the fact
that the real `OBJECTIVE_PARSE` call itself succeeded both times before the write failed.

**Post-fix verification:** `cd apps/api && uv run pytest` — **132/132 passing** (129 + 3 new
`test_llm_calls_atomicity.py`, minus zero — the `test_redaction.py` fix modified an existing test rather
than adding one). `make demo-reset && make demo` — byte-identical to every prior gate. `cd apps/web &&
pnpm lint && pnpm build` — clean, unchanged (no frontend files touched). **No live OpenAI call was made
to verify this fix** — the bug and its fix are entirely in the persistence layer below where the real API
call already succeeded; the fix was proven by direct reproduction against a real FK-enforced SQLite file
plus the new regression test, not by spending money to re-prove a provider-layer behavior that was never
broken.

---

## Final real smoke: Checkpoint G confirmed working end-to-end

A third real `make live-smoke` run, after the FK-ordering fix above, completed successfully — all four
Live LLM operations executed for real against OpenAI, the objective-parse persistence fix held under a
real run, and both retry paths a fixture-only smoke can't otherwise exercise fired for real and
recovered correctly. **No further Checkpoint G live smoke is required.**

**Configuration:** model `gpt-5.6-terra`, `reasoning_effort=low`, `llm_max_output_tokens=2048`.

**Real per-attempt provider behavior:**

| Operation | Attempt 1 | Attempt 2 |
|---|---|---|
| `objective_parse` | `TIMEOUT` | `transport_retry` → `OK` |
| `research_extraction` | `OK` | — |
| `score_explanation` | `PROVIDER_ERROR` | `transport_retry` → `OK` |
| `personalization` | `OK` | — |

Both retryable-transport statuses this checkpoint's flat retry loop was built to handle —
`TIMEOUT` (objective_parse) and a 5xx-shaped `PROVIDER_ERROR` (score_explanation) — occurred for real
against the live API in this run, each recovering via exactly one `transport_retry` attempt and landing
`OK`. This is the flat retry loop (`providers/live/openai_llm.py::OpenAILLMProvider.structured()`)
doing its designed job against genuine transient failures, not a simulated/scripted one — the same
mechanism `tests/test_live_openai_provider.py` proves offline against a scripted transport, now also
confirmed against the real thing. No schema repairs and no truncations occurred in this run — every
attempt that reached `OK` did so with a strict-schema-valid response on the first try after any
transport retry.

**Usage:** `tokens_in=2511`, `tokens_out=693`, `reasoning_tokens=0` where reported by the API. Pricing
remains intentionally unconfigured (`OPENAI_PRICE_*_USD_PER_MTOK` unset) — `estimated_cost_usd` and every
per-attempt `cost_usd` stayed `null`, exactly as designed; Groundwork made no attempt to compute or guess
a dollar figure.

**Final prospect: Sable Compute — `score=56`, `status=REJECTED`, `review verdict=FAIL`.** Guardrail
results:

| Check | Result |
|---|---|
| `claim_grounding` | **FAIL** — one generated composite outreach claim was not sufficiently supported by its cited evidence |
| `no_fabricated_contact` | PASS |
| `cross_prospect_leak` | PASS |
| `no_placeholders` | PASS |
| `duplicate_account` | PASS |
| `score_support` | PASS |
| `confidence_floor` | PASS |

This run's own guardrail printout (added in the first post-smoke hardening pass —
`live_smoke.py::_print_review()`) settles what the second real smoke's investigation could only infer
from code analysis: it was `claim_grounding`, specifically, and only `claim_grounding` — every other hard
check passed cleanly, consistent with that investigation's elimination reasoning (single-prospect run, no
fabricated contact fields anywhere in this codebase). **This REJECTED result is correct safety behavior,
not a smoke test failure.** The model produced valid, schema-conformant, on-topic structured output at
every step — the failure is not "the model broke" or "the pipeline broke." Deterministic grounding
(`domain/review.py`/`domain/grounding.py`, unchanged, byte-for-byte, this entire checkpoint) is what
caught a claim built by composing multiple grounded facts into a single sentence whose combined wording
no longer token-overlaps any one cited evidence snippet closely enough, and correctly withheld it from
being an approvable outreach draft. That is the guardrail doing exactly its documented job — the model
that wrote the draft does not get to grade whether its own claim is well-supported; a deterministic
check with no LLM in its path does, and it said no. A live system that let this through unreviewed would
be the actual defect.

**What this run specifically confirms, that the code-level fixes alone could not:**

- **The `create_play_with_attempts` FK-ordering fix holds under a real run.** `objective_parse` (with a
  real `TIMEOUT` + transport retry in the middle of it) persisted its `Play` and telemetry together
  without error — the exact path the second post-smoke fix repaired, now proven against a live,
  non-scripted failure sequence, not just the offline reproduction and regression test.
- **The real transport-retry path was exercised successfully** (`objective_parse`'s `TIMEOUT` →
  `transport_retry` → `OK`), for the first time against a genuine network condition rather than a
  scripted `httpx2.MockTransport` failure.
- **The real provider-error retry path was exercised successfully** (`score_explanation`'s
  `PROVIDER_ERROR` → `transport_retry` → `OK`), likewise for the first time against a real upstream
  condition.
- All four Live LLM operations (`objective_parse`, `research_extraction`, `score_explanation`,
  `personalization`) have now each completed successfully against the real API at least once, across
  the three real smoke runs in this checkpoint's history.

**No further Checkpoint G live smoke is required.** Every mechanism this checkpoint set out to prove —
real structured-output calls, the flat retry loop's transport-retry path, the objective-parse
transactional persistence fix, and the deterministic review gate holding against genuine (not scripted)
model output — has now been confirmed against the real API at least once. This is a documentation-only
update; no application code changed as part of recording these results.

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

**Checkpoint D's own deviations:**

- **New Play's "live parsed spec" preview creates a real `Play` row on every debounced edit**, not
  just on submit. There's no parse-only endpoint (§8's Objective Parser LLM agent doesn't exist yet,
  Checkpoint C's own noted deviation) and inventing one wasn't in scope for this checkpoint, so the
  form calls the real `POST /api/plays` 600ms after the user stops typing/adjusting controls, same as
  clicking Run Agents would. This does leave extra unused `Play` rows in the DB from abandoned edits
  (SQLite, resettable, `demo-reset` wipes them) — an accepted cost for showing the parser working
  live "beside the form" per §18/§4, rather than only after commit.
- **"Agents active N / 3" hardcodes the `3`** as `lib/constants.ts::MAX_CONCURRENT_PROSPECTS`, mirroring
  `config.py`'s `Settings.max_concurrent_prospects` default. No Checkpoint C endpoint returns this
  value (`GET /settings/providers` only reports `{mode, llm, search}`). The numerator is never
  hardcoded — it's `prospects.filter(stage !== DISCOVERED && !terminal).length`, computed live every
  render. If `max_concurrent_prospects` is ever changed from 3, this constant needs a matching edit (or
  a future checkpoint adds it to `GET /settings/providers` and this reads it instead).
- **`ActivityStream` never renders a `step.failed` event because the engine never emits one** — §19
  lists `step.failed` as a documented event type, but Checkpoint B/C's engine raises straight through a
  non-optional step's exhausted retries into `prospect.completed` with `status: FAILED` and the real
  exception message (see `engine/runner.py`'s `except Exception` in `execute_run`'s inner `one()`).
  This is a pre-existing gap, not something to paper over with a frontend-only event: the activity
  stream's `"{company} · failed — {error}"` line for a `prospect.completed` failure uses that real
  error string; no attempt count or synthetic wording is fabricated to match the plan's illustrative
  `"failed after 3 attempts"` example text.
- **No frontend automated tests added.** Checkpoint D's plan entry doesn't list a test file (unlike
  every backend checkpoint); verification is build/lint gates plus the browser walkthroughs recorded
  above. If a future checkpoint wants component/E2E tests, `apps/web/package.json` has no test runner
  configured yet — that's a new dependency decision, not an oversight to silently fix.
- **`app/page.tsx` (root `/`) redirects to `/plays/new`** rather than staying the Checkpoint A
  health-check card — there is intentionally no dashboard/home page in P0 (§5), and an unreachable
  root route would be a broken-feeling entry point for the founder demo.

**Checkpoint F's own deviations:**

- **`reliability.per_step_success_rate` added to the evaluation payload** — not explicitly named as a
  field in §16, which lists "per-step success rate" as prose under Reliability without specifying the
  JSON shape. Implemented as the smallest faithful reading: one number per step name, computed from
  data the endpoint already loaded, purely additive (no existing field changed, no existing test
  touched).
- **`PlaySpec.target_count`/`PlayCreateRequest.target_count` defaults changed `6 → 7`** — not a scope
  addition, a correction: the fixture pack (`demo_pack.yaml`) and every backend test already treated 7
  as canonical; only the two Pydantic model defaults and the New Play form's `useState` were still on
  the pre-Sable-Compute value of 6. `docs/IMPLEMENTATION_PLAN.md` §4's narrative prose still says "six
  prospect rows" (written before Sable Compute was added in Checkpoint B) — `DEMO_SCRIPT.md` was written
  to match what the product actually does (seven) rather than copying that now-stale prose forward.
- **New Play's default ICP overrides expanded from 4 fields to the full canonical set** (see "What
  Checkpoint F added" above for the full story) — this is a bug fix, not a UI scope expansion: the form
  still exposes exactly the four controls §18 specifies (target industries, size min/max, min score);
  the additional fields are sent as fixed defaults matching the fixture pack's own `play_spec`, the same
  way `tests/api_helpers.py::DEMO_ICP_OVERRIDES` already did for the backend test suite. No new form
  field was added.
- **No new backend test file for the `target_count`/override default changes.** Existing tests already
  pass `target_count`/`icp_overrides` explicitly (`tests/api_helpers.py`, `test_isolation.py`,
  `test_scoring.py`), so they were unaffected by the default change and remain the coverage for those
  code paths; the *frontend* default was verified by browser rehearsal (§32 checklist), not a new
  Playwright/component test suite — consistent with Checkpoint D/E's precedent of no frontend automated
  tests in this repo.
- **`app/favicon.ico` is a hand-built minimal ICO** (16×16, 32bpp, solid two-color square matching
  `app/icon.svg`), constructed with Python's `struct` module directly rather than an image tool — no
  image-generation tooling (ImageMagick, PIL) was available in this environment. Fine for its only job
  (stop the browser's automatic `/favicon.ico` probe from 404ing); replace with a designed asset
  whenever the product gets real brand treatment.

**Checkpoint G's own deviations:**

- **The test suite is a representative subset of the exhaustive list in the task instructions, not a
  literal one-test-per-bullet mapping.** 51 new tests cover every named invariant (flat retry-loop
  composition and its property test, strict schema compatibility, output cap sizing, provider-layer
  purity, objective-parse transaction/fallback, redaction, concurrent `RunBudget` accounting, the
  process-wide semaphore, a fake-live end-to-end run, additive demo `llm_calls`, no-credentials Demo
  Mode) but several listed items (e.g. "invented/foreign citation grounding rejection",
  "outbound request prospect isolation") are exercised by *existing*, unmodified Checkpoint B–E tests
  (`test_isolation.py`, `test_review.py`) rather than a new Checkpoint-G-specific duplicate, since the
  underlying grounding/review/isolation code is untouched by this checkpoint. Flagged explicitly rather
  than silently claiming a new named test exists for every bullet.
- **`httpx2` is a genuinely separate installed package from `httpx`**, not a typo or an alias — the
  pinned `openai==3.6.0` SDK depends on it directly (confirmed by inspecting
  `site-packages/openai/_client.py`'s own imports). `tests/live_helpers.py` and
  `providers/live/runtime.py` import `httpx2` specifically for the OpenAI-facing mock transport;
  everything else in the repo (FastAPI, the existing `httpx` test client in `conftest.py`) keeps using
  plain `httpx`. Not a decision — a fact about the installed environment, verified rather than assumed.
- **Live Mode's per-step budget does not reuse `DEMO_BUDGET`'s literal timeout/retry values** —
  `LIVE_STEP_TIMEOUT_S=45` (vs. Demo's `2.0`) and `LIVE_RUN_WALL_CLOCK_TIMEOUT_S=600` (vs. Demo's
  `180.0`), matching the plan's own named hard bounds for real network calls. Research/personalize
  retry *counts* stay the same shape (2/1) as Demo — only the timeouts differ, since a real OpenAI call
  can legitimately take much longer than a fixture-derived one.
- **`RunRow.provider_profile` is computed once, at run creation, and persisted** (a new JSON column)
  rather than recomputed live from a `RunBudget` instance on every read — a per-run `RunBudget` is
  in-process state that doesn't survive past the run's own coroutine, so "reconstruct what actually ran"
  has to be a snapshot taken when the run starts, not a live query. `soft_budget_usd`/
  `soft_budget_enforceable` in that snapshot reflect the threshold *configured* at run start, not a
  live "amount spent so far" figure — `evaluation/metrics.py`'s `llm_usage.estimated_cost_usd` is the
  field for actual spend, computed on read from `llm_calls` like everything else in that endpoint.
- **The objective parser's fallback path is exercised with a direct `LLMProvider` fake in
  `tests/test_objective_parser.py`** (not always the real `OpenAILLMProvider` against a scripted
  transport) for the pure-function unit tests (`parse_objective()` zero-DB-writes,
  fallback-on-error, user-overrides-win) — faster and more direct for testing `parse_objective()`'s own
  logic in isolation. The one transactional-persistence test
  (`test_play_and_llm_calls_created_in_one_transaction`) does use the real `OpenAILLMProvider` against
  `httpx2.MockTransport`, so the actual wire-shaped path is still proven end-to-end at least once.
- **No screenshots of a completed Live run** (Run Detail/Quality/Prospect Detail in Live Mode) — this
  environment has no `OPENAI_API_KEY` configured and none was provided by the user this session, so
  there is no way to produce a *real* live screenshot without either fabricating one (not done) or
  standing up a second mocked HTTP layer behind the actual running dev server (out of scope for this
  checkpoint's verification budget). The fake-live path is instead proven at the test level
  (`test_live_pipeline_integration.py`, full `execute_run` through a scripted transport into real DB
  rows) and the *unavailable* Live UI state is shown in a real screenshot (New Play, Live disabled with
  its explanation).
- **`Panel`/`Badge`/`Button` UI primitives were reused as-is** for the New Play Live controls and the
  new `ModelUsagePanel` — no new primitive was added, per §18's "commit once, don't revisit" on the
  visual language.

---

## Next task

**Checkpoint G is complete. Do not begin Checkpoint H without the user explicitly asking.**
All Checkpoint A–F (P0) behavior remains unchanged and verified byte-identical; Checkpoint G adds real
OpenAI LLM execution (fixture-backed search) behind the existing provider seam, fully tested against a
scripted transport, with zero live network calls made by any automated test.

If/when Checkpoint H (live web search) is authorized, the seams are already in place:
`providers/registry.py::build_provider_bundle` is where a `TavilySearchProvider` (or similar) would be
wired in for Live Mode instead of the current `DemoSearchProvider`; `models/schemas.py::Evidence`'s
`origin=LIVE_FETCH` path (with a real `source_url`) is built and tested
(`test_fixture_provenance.py`) but currently untriggerable — every evidence row in the repo today is
still `DEMO_FIXTURE`, in both Demo and Live Mode, because Checkpoint G is explicitly "LIVE LLM · FIXTURE
SEARCH." `providers/profile.py`'s `synthetic_search: true` / `evidence_origin: "DEMO_FIXTURE"` fields
would flip once a real search provider lands. Beyond Checkpoint H, `docs/IMPLEMENTATION_PLAN.md` §5's
remaining order still applies: **deployment → MCP shim → polish**. Concretely:

1. **Live search** (Tavily or similar) — the other half of Live Mode; needs real `source_url` handling
   to actually exercise the `LIVE_FETCH` evidence-card path that's built but currently untriggerable.
2. **Deployment** — currently local-only by design (§27); optional.
3. **MCP shim** — exposing Groundwork's play/run/prospect operations as MCP tools.
4. **Polish backlog** (not required, noted for completeness):
   - A graphical trace waterfall instead of `TraceTable` (explicitly cut from P0, §5/§34).
   - A standalone cross-run evaluation page (the Quality tab is the P0 scope; §16 names a standalone
     page as P1).
   - `POST /runs/{id}/cancel` (explicitly cut from P0, §5).
   - Draft edit/regenerate on outreach.
   - Frontend automated tests — `apps/web/package.json` has no test runner configured; every checkpoint
     from D onward has verified via build/lint gates and browser rehearsal instead. Worth adding
     Playwright/component tests as first P1-adjacent hardening if this project continues past the
     interview.
   - Expose `max_concurrent_prospects` via `GET /settings/providers` so
     `lib/constants.ts::MAX_CONCURRENT_PROSPECTS` stops needing to be hand-kept in sync with
     `config.py`.
   - A real per-run `plan.created` event / rendered plan panel, if a future checkpoint wants the DAG
     itself surfaced in the UI (currently `RunRow.plan` stays `[]` since the pipeline is the same fixed
     7-step list every run).

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
- Checkpoint A's `do not touch` list for `main.py`/`config.py`/`db.py`'s pre-existing shape still
  applies (`config.py`/`db.py` grew as anticipated: WAL pragma already existed and is unchanged, added
  `create_all()`/`drop_all()` only). Its original note about `apps/web/` is superseded — Checkpoint D
  is exactly the session that was supposed to build there.
- **Checkpoint D's own do-not-touch:** the visual language locked in `app/globals.css`/`app/layout.tsx`
  (zinc/indigo/JetBrains Mono, no gradients/glows) — §18 says commit once, don't revisit; `lib/
  useRunStream.ts`'s manual-reconnect-over-native-EventSource-retry design and its synchronous
  terminal-status handling for `run.completed`/`run.failed` (removing either reintroduces the
  duplicate-replay-on-reconnect bug and the false-positive-reconnect-on-clean-close race respectively,
  both hit and fixed during this checkpoint's own testing); `lib/constants.ts::MAX_CONCURRENT_PROSPECTS`
  should track `config.py` by hand until/unless a future checkpoint exposes it via `GET
  /settings/providers`, not be silently guessed at a different value.
- **Checkpoint E's own do-not-touch:** `EvidenceCard.tsx`'s origin gate
  (`origin === "LIVE_FETCH" && source_url`) is the frontend half of the §12 provenance invariant —
  don't relax it to show a link for any other origin, even if a future fixture accidentally carries a
  `source_url`. `SignalList.tsx`'s `(type, summary)` grouping is display-only (a `×N` badge); if a
  future checkpoint changes what research extraction persists per fact, re-verify this grouping still
  makes sense rather than assuming it does. `ApprovalBar`'s client-side decidable-status gate
  (`{PASS, NEEDS_REVIEW, REJECTED}`) must stay in sync with `routers/prospects.py`'s
  `_DECIDABLE_STATUSES` — if that set changes on the backend, update both.
- **Checkpoint F's own do-not-touch:** `app/plays/new/page.tsx`'s `overrides()` default ICP fields
  (`excluded_industries`, `adjacent_industries`, `target_funding_stages`, `target_technologies`,
  `persona_titles`, `min_confidence`, and the `sizeMin`/`sizeMax`/`industries` `useState` defaults) must
  keep matching `demo_pack.yaml`'s own `play_spec` / `tests/api_helpers.py::DEMO_ICP_OVERRIDES` exactly
  — this is what makes the canonical demo run through the real UI reproduce the documented reference
  scores. If the fixture pack's `play_spec` or any company's `industry`/`employee_count` ever changes,
  re-verify this default set and the numbers in `docs/DEMO_SCRIPT.md` together, in the same change —
  they drifted apart once already (see "What Checkpoint F added") and it was silent until someone
  actually ran the product. `PlaySpec.target_count`'s default of `7` must keep matching the fixture
  pack's real company count for the same reason. `MetricGrid.tsx`/`GuardrailPanel.tsx` read `null` as
  "no data yet", never a fabricated placeholder — keep that contract if either is extended.
- **Checkpoint G's own do-not-touch:** the flat retry loop in
  `providers/live/openai_llm.py::OpenAILLMProvider.structured()` — ONE `while True` loop, ONE outbound
  call site (`_issue()`), counters initialized once. Do not "simplify" this into nested retry helpers or
  a `tenacity`-style decorator — that reintroduces the `(1+T)*(1+S)` explosion the plan explicitly
  rejects, and `transport_retry_index`/`schema_round` must keep the exact semantics documented at the
  top of that file (index never resets on repair; round flips 0→1 exactly once) or
  `test_transport_budget_never_resets_after_schema_repair` and the randomized property test will start
  lying. `providers/base.py`'s `STEP_RETRYABLE = (ProviderTimeout, ProviderUnavailable,
  ProviderRateLimited)` is the complete, exhaustive step-level-retryable set — do not add a fourth type
  without updating both the constant and its docstring. `engine/llm.py::call_structured()` is the ONLY
  place attempt telemetry is persisted; do not add a second write path, and do not let `providers/*`
  import a repository or SQLAlchemy (`test_provider_purity.py` enforces this by source inspection, not
  just convention). `observability/redact.py::redact()` must stay the single choke point every
  error-to-string path routes through before persistence — do not add a new `error_message`/
  `validation_error` write site that bypasses it. `DEMO_BUDGET` in `engine/budget.py` must keep
  reproducing Checkpoint B–F's literal constants exactly (`2.0`s timeouts, `2`/`1` retries,
  `(0.4, 0.8, 1.6)` backoffs) — it's what keeps the canonical demo byte-identical; Live-only bounds
  belong in a *separate* budget (`api/run_service.py::live_budget_from_settings()`), never by mutating
  `DEMO_BUDGET`'s defaults. `LLM_MAX_OUTPUT_TOKENS=2048` is measurement-selected (see "What Checkpoint G
  added" → Phase 4) — if any operation's prompt or schema changes meaningfully, re-measure with
  `tests/test_output_cap_sizing.py`'s methodology rather than bumping the number by feel.
- **Post-smoke addition:** `repositories/llm_calls.py::create_play_with_attempts`'s
  `session.add(Play)` -> `await session.flush()` -> `session.add(LLMCallRow...)` -> `session.commit()`
  ordering is load-bearing, not stylistic — removing the `flush()` reintroduces the real
  `FOREIGN KEY constraint failed` bug the second live smoke test hit (`models/tables.py` has no ORM
  `relationship()` anywhere, so nothing else orders the inserts). If any future method in this file
  writes a new row that references an id created earlier in the same transaction, it needs the same
  `flush()`-before-referencing-insert pattern — `tests/test_llm_calls_atomicity.py` guards this specific
  method's contract but won't catch a new method repeating the mistake. `tests/conftest.py::_enable_wal`
  must keep setting `PRAGMA foreign_keys=ON` (mirroring `db.py`'s real one exactly) — removing it would
  silently make the whole test suite blind to foreign-key violations again, the way it already was once.
