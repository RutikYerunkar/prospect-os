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
| **F — Quality + hardening** | *this commit* (branch `claude/eager-wright-pvdo5h`) | Quality tab (`MetricGrid` + `GuardrailPanel`) backed by the existing evaluation endpoint; a real demo-consistency bug found and fixed (New Play's default ICP overrides silently diverged from the fixture pack, changing both prospect count and Northwind Labs' score); visual polish (friendlier terminal states, humanized activity labels, obvious synthetic-evidence badges, structural-dimension score clarity); two clean-reset rehearsals through the real UI; README + DEMO_SCRIPT finalized. **P0 COMPLETE.** |

---

## Current checkpoint

**F — Quality + hardening. P0 COMPLETE.** All six checkpoints (A–F) are done; the full §32
founder-demo checklist passes against the real running stack, rehearsed twice from a clean
`make demo-reset`.

A future session should read `CLAUDE.md`, `docs/ARCHITECTURE.md`, and this file before starting any
P1 work (§5 of `docs/IMPLEMENTATION_PLAN.md`, in order: OpenAI provider → live search → deployment →
MCP shim → polish). See "Next task" below for what's actually next.

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

---

## Next task

**P0 is complete.** All six checkpoints (A–F) are done, the full §32 founder-demo checklist passes
against the real running stack, and both rehearsals were reproducible from a clean `make demo-reset`.
There is no required next task — a future session should not start P1 work without the user explicitly
asking for it (per this file's own instructions and `CLAUDE.md`'s checkpoint protocol).

If/when P1 is authorized, `docs/IMPLEMENTATION_PLAN.md` §5 gives the order: **OpenAI provider → live
search → deployment → MCP shim → polish** (waterfall trace, standalone eval page, cancellation,
draft edit/regenerate, LLM tone advisor). Concretely, in priority order:

1. **OpenAI provider** (`providers/live/`) — implement `LLMProvider`/`SearchProvider` for real, behind
   the same Protocols `providers/demo/` already satisfies. `providers/registry.py`'s `NotImplementedError`
   for `Mode.LIVE` is the exact seam; the API layer already rejects `mode: "live"` at the door
   (`PlayCreateRequest.mode: Literal["demo"]`), so this is additive, not a rewrite.
2. **Live search** (Tavily or similar) — the other half of Live Mode; needs real `source_url` handling
   to actually exercise the `LIVE_FETCH` evidence-card path that's built but currently untriggerable
   (every evidence row in the repo today is `DEMO_FIXTURE`).
3. **Deployment** — currently local-only by design (§27); optional.
4. **MCP shim** — exposing Groundwork's play/run/prospect operations as MCP tools.
5. **Polish backlog** (not required, noted for completeness):
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
