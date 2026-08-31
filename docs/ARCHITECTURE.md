# Groundwork — Architecture

The document to reread at 8am before the interview. For full rationale, tradeoffs, and the
section-by-section build plan, see `docs/IMPLEMENTATION_PLAN.md`. This file is the condensed map:
the diagram, the three claims that carry the project, the deterministic-vs-LLM split, the isolation
model, and the founder discussion points, so you can refresh in five minutes instead of forty.

---

## The engine

```
                       ┌─ deterministic ─┐
  Objective ──► PlaySpec ──► Discovery ──► dedupe ──► [fan-out]
                                                          │
        ┌─────────────────────────────────────────────────┴───────────────┐
        │  per prospect, isolated ProspectContext, bounded concurrency     │
        │                                                                  │
        │   Research ─► Signals ─► Enrich ─► Score ─► Contact ─►           │
        │   Personalize ─► Review ─► [awaiting human approval]             │
        └──────────────────────────────────────────────────────────────────┘
                                    │
                           run_events (append-only) ──► SSE ──► UI
```

```
┌──────────────────── apps/web (Next.js, all client components) ────────────────────┐
│  New Play · Run Detail ⭐ (Board tab | Quality tab) · Prospect Detail              │
│  useRunStream(runId): EventSource + client reducer + reconcile-on-reconnect        │
└──────────────────────────────────┬────────────────────────────────────────────────┘
                                   │ REST (JSON) + SSE
┌──────────────────────────────────┴──────── apps/api (FastAPI, one process) ───────┐
│  routers ─► services ─► RunExecutor                                                │
│      ┌─────────────────────┴────────────────────────┐                              │
│      │ global semaphore(3) · per-provider semaphores │                              │
│      │ asyncio.gather(*prospect_pipelines,           │                              │
│      │                return_exceptions=True)        │                              │
│      └─────────────────────┬────────────────────────┘                              │
│              ProspectContext ── Step chain (topo-ordered)                           │
│  domain/ (pure)      providers/ (Protocol)      observability/                      │
│  scoring · dedupe    demo/ | live/              trace spans · event bus             │
│  grounding · review                                                                 │
└──────────────────────────────────┬────────────────────────────────────────────────┘
                                   │ SQLAlchemy async
                          SQLite (WAL) — single file, resettable
```

Two processes, one command: `make dev` runs uvicorn on `:8000` and Next on `:3000`.

---

## The three claims that carry the project

1. **The orchestrator is deterministic; LLMs are used only where ambiguity is genuinely valuable.**
   The pipeline DAG is code, not a model's opinion. ICP scores are a pure weighted rubric — the LLM
   writes the explanation *from* the numbers and cannot change them. Review contains no LLM at all.
2. **Per-prospect state isolation is structural, not conventional.** Each prospect gets a
   `ProspectContext` that is the only state its workers touch, plus a runtime guardrail
   (`cross_prospect_leak`) that checks every outreach draft for contamination. Isolation is enforced
   and observable, not promised — see `test_isolation.py`.
3. **Evidence is first-class and provenance is typed.** Every claim carries `evidence_ids`; every
   evidence row carries an origin (`DEMO_FIXTURE` / `LIVE_FETCH` / `LLM_INFERENCE`). Synthetic
   evidence is structurally prevented from ever carrying a real-looking URL (a Pydantic model
   validator, not a convention).

Demo Mode is not frontend fakery — it swaps `LLMProvider` / `SearchProvider` implementations behind
Protocols. Fixtures contain evidence, not verdicts: every score, status, duplicate flag and review
result on stage is computed by the real engine from that evidence.

---

## Deterministic vs LLM responsibilities

| Component | Classification | Rationale |
|---|---|---|
| Objective → PlaySpec | **LLM** (deterministic fallback) | Real ambiguity; schema-validated; cheap to fall back. |
| Pipeline construction | **Deterministic** | Known DAG. Variance is a cost, not a feature. |
| Discovery | **Deterministic** | Provider I/O + normalization. |
| Research extraction | **LLM** | Unstructured → `ResearchFacts`, Pydantic-validated, retried on schema failure. |
| Signal detection | **Hybrid** | LLM proposes; a deterministic verifier confirms the cited span actually occurs in the source and belongs to this prospect. |
| Enrichment | **Deterministic** | Field precedence + `UNKNOWN` sentinel. Never interpolate. |
| ICP scoring | **Deterministic** (LLM writes explanation only) | Reproducible, auditable, tunable, testable. |
| Contact resolution | **Deterministic** | Generative contact data is the most dangerous hallucination in GTM. |
| Personalization | **LLM** | Where ambiguity is genuinely valuable. |
| **Review verdict** | **Deterministic — no LLM** | The model that wrote the draft can't grade the draft. |
| Dedupe | **Deterministic** | Normalization + comparison. |
| Evaluation metrics | **Deterministic** | Computed on read from records. |

**The one-line thesis:** use LLMs for ambiguity and language; use deterministic code for arithmetic,
identity, and policy.

---

## Prospect state isolation & concurrency

One coroutine per prospect, each running its own topologically-ordered `Pipeline` against its own
`ProspectContext`, fanned out under a bounded semaphore:

```python
async def execute_run(run_id: str) -> None:
    prospects = await discover_and_dedupe(run_id)
    gate = asyncio.Semaphore(settings.max_concurrent_prospects)   # default 3

    async def one(p: Prospect) -> ProspectOutcome:
        async with gate:
            ctx = ProspectContext.for_prospect(run_id, p)         # ← isolation boundary
            return await PROSPECT_PIPELINE.execute(ctx)

    tasks = [asyncio.create_task(one(p)) for p in prospects]
    await asyncio.gather(*tasks, return_exceptions=True)          # ← failure isolation
    await finalize_run(run_id)
```

`gather(..., return_exceptions=True)` instead of `asyncio.TaskGroup` is deliberate: `TaskGroup`
cancels every sibling on the first unhandled exception (structured concurrency, correct for one
indivisible operation); prospects are independent units where partial success is the desired
outcome.

**Four mechanisms prevent cross-prospect contamination:**
1. No shared mutable state — every mutable field lives on `ProspectContext`.
2. Prompt envelopes are built only from `ctx` — no conversation object accumulates history.
3. `test_isolation.py` — two prospects with canary tokens; zero cross-contamination asserted.
4. Runtime guardrail `cross_prospect_leak` — scans every draft for another prospect's name/domain,
   on every run, on real data.

Per-step reliability: `asyncio.wait_for` per attempt, exponential backoff + jitter, one `agent_tasks`
row per attempt, idempotency key `(run_id, prospect_id, step_name)`, a run-level 180s watchdog.
Cancellation is P1.

Full detail: `docs/IMPLEMENTATION_PLAN.md` §10.

---

## Evidence, scoring, and review — the audit chain

- **Evidence** (`docs/IMPLEMENTATION_PLAN.md` §12): scoped to one prospect, typed by origin, and a
  model validator forbids a `source_url` on anything but `LIVE_FETCH` — fabricated sources are
  structurally impossible, not merely discouraged.
- **ICP scoring** (§13): eight weighted dimensions, pure arithmetic, an evidence gate that zeroes
  unsupported dimensions, a hard disqualifier modifier, `rubric_version` stored on every score. The
  LLM writes the sentence under the table; it cannot move a number.
- **Review** (§14): seven deterministic checks (`claim_grounding`, `no_fabricated_contact`,
  `cross_prospect_leak`, `no_placeholders`, `duplicate_account`, `score_support`,
  `confidence_floor`). No LLM anywhere in this path.

Together these make "why did this score 91 and not 75" and "how do you stop hallucinated claims"
answerable by pointing at a table, not by trusting a model's self-report.

---

## Founder discussion points (see plan §29 for the full text of each)

1. Why isolate prospect context — correctness and scalability.
2. Why `gather(return_exceptions=True)` and not `asyncio.TaskGroup`.
3. Why scoring is deterministic — auditability, tunability, testability.
4. Why the orchestrator isn't an agent — the DAG is known; a planner would select among templates.
5. Why there is no LLM in review at all — the author is the worst grader of its own output.
6. Why demo mode fakes at the provider boundary, not the frontend.
7. Why evidence is first-class, and why synthetic evidence can't carry a real-looking URL.
8. Why SQLite, and exactly where it stops working (the single-writer lock, ~20–30 concurrent
   prospects).
9. Why SSE over a durable event log instead of raw WebSockets or polling.
10. Prompt injection in GTM — the injectable surface and the decision surface are disjoint.
11. What breaks first at scale — SQLite's write lock, then provider rate limits, not the
    orchestration.
12. Online vs offline eval, and the feedback loop from rejected prospects.

---

## Where things live

See `docs/IMPLEMENTATION_PLAN.md` §22 for the full folder structure. The load-bearing boundary:
`domain/` (scoring, dedupe, grounding, review) is pure — no I/O, no imports from `providers/` or
`repositories/`. That's what keeps the four modules that must be *right* unit-testable in
milliseconds, and it's the answer to "can I use your scorer in a batch pipeline?": `import`.
