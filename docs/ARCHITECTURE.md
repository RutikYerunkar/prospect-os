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

## Live Mode (Checkpoint G)

**LIVE LLM · FIXTURE SEARCH.** `providers/live/openai_llm.py::OpenAILLMProvider` is a real OpenAI
Responses-API client (strict Structured Outputs) behind the identical `LLMProvider` Protocol Demo Mode
satisfies — same pipeline, same domain layer, same isolation. Search stays `DemoSearchProvider`; live
web search is Checkpoint H, not built. No `OPENAI_API_KEY` configured → Live Mode 422s cleanly
(`ProviderNotConfigured`), never a silent fallback to Demo.

One logical LLM call is ONE flat retry loop (`engine/llm.py`/`providers/live/openai_llm.py`), never
nested `(1+T)*(1+S)` retries: at most `1 + LLM_MAX_TRANSPORT_RETRIES + LLM_MAX_SCHEMA_RETRIES` provider
attempts, each persisted to a new `llm_calls` table (one row per attempt, full telemetry, redacted of
secrets). Hard bounds cap prospects/run, concurrency (a process-scoped `asyncio.Semaphore`, shared
across simultaneous runs), and output tokens; a soft (never hard) per-run spend threshold only enforces
once real pricing is configured. The Objective Parser is the fourth Live LLM operation — it runs before
any `Play` row exists, falls back deterministically on any provider failure, and its telemetry is
written in the same DB transaction as the `Play` it belongs to.

Full detail, SDK facts verified against the installed `openai` package, and the output-token-cap
measurement: `docs/PROGRESS.md`'s Checkpoint G section.

---

## H1 — demo-neutral, real-company-safe foundation (no live search yet)

Checkpoint H is split: **H1** hardens the domain/search/provenance/scoring foundation so it can safely
accept an arbitrary real company later; **H2** (not built) adds the actual live search vendor. Nothing
in H1 performs live web search or writes a vendor adapter.

**Two real bugs fixed.** (1) `research.py` used to append `Evidence` *before* the LLM call, so a
step-level retry appended the same sources' evidence a second time — fixed by separating retrieval
state (`ctx.sources`, fetched at most once) from accepted Evidence state (`ctx.evidence`, committed by
one assignment, only on a successful extraction, with deterministic uuid5 ids). (2) `cross_prospect_leak`
used a plain substring check, false-positiving on real short company names ("Ramp" inside "cramping")
— replaced with a word-boundary-aware match.

**Offline domain normalization** (`domain/psl.py`) routes through a pinned `tldextract`
(`suffix_list_urls=()`, `include_psl_private_domains=True`) — no runtime PSL network fetch, ever;
`acme.co.uk` keeps its two-label suffix, `acme.github.io` stays distinct from bare `github.io`, a bare
suffix is rejected as having no company identity.

**Company profile facts are field-provenance independent.** `IndustryProfileFact`/
`EmployeeCountProfileFact` each carry their own `evidence_ids`, populated only after their own
independent deterministic grounding (`engine/steps/signals.py`): industry classification against a
Play-derived allowed-category set (`domain/industry.py`, `OTHER` vs `UNKNOWN` distinct), employee count
against an explicit numeric match in the cited text (`domain/grounding.numeric_claim_supported` — never
inferred from "a large team"). `domain/scoring.py`'s `industry_fit`/`size_fit` read *only* these grounded
facts now — the old "always-supported from `CompanySeed`" exemption is gone, closing the "naked seed
metadata earns score support" gap. `DimensionScore.support` is now tri-state
(`SUPPORTED`/`UNSUPPORTED`/`UNKNOWN`); `UNKNOWN` is excluded from the confidence denominator entirely.
Exclusion policy is tri-state too (`ExclusionEvaluation`): `UNKNOWN` (industry never grounded) forces
`NEEDS_REVIEW` in `runner.py::_derive_final_status` rather than silently passing — without adding an
eighth review guardrail; the seven deterministic checks are unchanged.

**Retrieval provenance is now persisted and deduplicated.** `source_documents` (one row per retrieval
*occurrence*) and `search_calls` (one row per provider call attempt) are new, additive tables —
`engine/search.py::call_search()` owns their persistence, exactly like `engine/llm.py::call_structured()`
owns `llm_calls`, and stays out of the SSE `run_events` log. `domain/source_identity.py` computes source
identity (canonical URL, or `source_ref` when there's no URL — the required Demo Mode fallback) and picks
a deterministic winner per identity group (`select_winners`) — the same URL returned by three queries
persists as three occurrences but contributes at most one `Evidence` row, with deterministic, idempotent
uuid5 Evidence ids (`domain/source_identity.evidence_id_for`).

**`SearchProvider` contract refined, no vendor added.** `discover`/`resolve_domain`/`fetch_sources` each
return their payload alongside `SearchAttemptTelemetry` (`providers/base.py`); `DemoSearchProvider` is
ported to it (Phase 13) with zero credentials. `domain/query_plan.py`/`domain/discovery.py` are pure,
offline, versioned query-template and identity-gate primitives for H2 — never exercised against a real
provider in H1. `scripts/search_spike.py` exists as a manual, opt-in fact-finding script for H2's Tavily
SDK — never run automatically.

Full detail, canonical-output verification at every phase gate, and deviations: `docs/PROGRESS.md`'s H1
section.

---

## H2 — real live web search (`LIVE LLM · LIVE SEARCH`)

H2 replaces Checkpoint G's fixture-backed search with a real `providers/live/tavily_search.py::
TavilySearchProvider` (pinned `tavily-python==0.8.0`, `AsyncTavilyClient`, process-scoped
`LiveSearchRuntime` created once in `main.py`'s lifespan exactly like `LiveProviderRuntime`).
**NEW Live Mode requires BOTH a configured OpenAI runtime AND a configured Tavily runtime** — never a
silent fallback to fixture search for either half; `api/routers/plays.py::start_run` 422s cleanly if
either is missing. A historical Checkpoint G run's `provider_profile` JSON (`LIVE LLM · FIXTURE SEARCH`)
is a persisted snapshot, never rewritten — it renders exactly as it was recorded.

**Real multi-stage discovery** (`engine/discovery.py::discover_live()`, invoked by
`engine/runner.py::discover_and_dedupe()` only when the wired search provider sets
`requires_llm_discovery = True`; Demo Mode's single-shot `discover()` path is untouched):

- **Stage A** — bounded, deterministic query plan (`domain/query_plan.py`, `LIVE_MAX_PLAN_QUERIES_PER_RUN`)
  issued as real Tavily searches (`TavilySearchProvider.raw_discover()`). Persisted as
  `source_documents` occurrences (run-scoped, `prospect_id=NULL` — no prospect exists yet) and
  `search_calls` telemetry, exactly like every other retrieval.
- **Stage B** — `LLMOperation.DISCOVERY_EXTRACTION`: bounded search-result excerpts (opaque refs, NO
  URLs) → candidate company names + supporting refs (`prompts/discovery_extraction.py`). Server
  independently re-verifies every candidate before it survives: every cited ref must have been served
  this call, and the company name must be textually supported by the cited excerpt(s)
  (`domain/discovery.py::company_name_textually_supported`, token-overlap, not a bare LLM assertion).
- **Stage C** — one deterministic domain-resolution query per surviving candidate
  (`"<company_name> official site"`). The engine — never the model — maps a served candidate's URL to a
  canonical domain (`domain/discovery.py::resolve_candidate_domain`: safety + PSL normalization + served-
  domain check + non-aggregator). Exactly one structurally-safe, label-matching candidate → accepted
  deterministically, zero LLM calls. Otherwise → the bounded `LLMOperation.DOMAIN_SELECTION` fallback,
  which may only select among refs already independently verified safe — the model can return `null`
  (a legitimate "unresolved," not an error) but can never author a domain itself.
- **Stage D** — the identity gate is Stage C's own served-ref/safety/aggregator check plus this module's
  domain-uniqueness dedupe; a candidate that can't establish a safe canonical domain is dropped, never
  turned into a fake prospect, and never consumes a `target_count` slot.

Every rejection/acceptance reason is emitted as a `run_events` row (`discovery.candidate_rejected`,
`discovery.domain_resolved`), replayed both as Activity Stream narrative and as `/evaluation`'s
`search_quality.discovery_rejection_reasons`/`domain_resolution_method_counts` — no second telemetry
table invented for something this lightweight.

**Real per-company retrieval** (`TavilySearchProvider.fetch_sources()`, called through the *same*
`engine/search.py::call_search()` seam `engine/steps/research.py` already used in H1 — no engine-level
change needed here): deterministic, domain-scoped category queries (funding / careers / leadership,
`include_domains=[canonical_domain]`, `domain/query_plan.py::build_source_queries`, bounded at
`LIVE_MAX_SOURCE_QUERIES_PER_PROSPECT`) → the exact same deterministic winner selection H1 built
(`domain/source_identity.py::select_winners`) → ONE batched Tavily `extract()` call per prospect for
the winners only (never one call per URL, never every discovery result blindly). A failed URL inside
that batch (`failed_results`) degrades that one source (`PARTIAL_EXTRACTION` telemetry, `SourceStatus.
PARTIAL`), never the whole prospect. Groundwork never issues an arbitrary `httpx.get(result_url)` —
provider-managed extraction only.

**Evidence provenance, fixed for real.** `engine/steps/research.py` used to hardcode
`origin=EvidenceOrigin.DEMO_FIXTURE, source_url=None` on every Evidence row regardless of what actually
produced it — harmless while only `DemoSearchProvider` existed, but it would have silently mislabeled
every real `TavilySearchProvider` result as synthetic fixture data (and dropped its real URL) the moment
Live search existed. H2 fixes this: Evidence now reads `origin`/`source_url`/`retrieved_at` off the
winning `SourceDocument` itself — `Evidence`'s own §12 model validator still enforces the invariant
structurally (only LIVE_FETCH may carry a URL) regardless.

Published dates stay nullable and never inferred (the verification spike found no reliably-populated
`published_date` field on ordinary Tavily search results); persisted content is a bounded excerpt
(`LIVE_MAX_SOURCE_EXCERPT_CHARS`), never a raw page body; Tavily's `include_usage` credits are mapped
into search telemetry independently of whether a trustworthy USD rate is configured (`cost_usd` stays
null without one — the UI shows "—", never "$0.00"; hard call/query/result/extract caps are the real
spend control). Full verified SDK facts, response-shape mapping, and the pinned version:
`docs/PROGRESS.md`'s H2 section.

---

## Where things live

See `docs/IMPLEMENTATION_PLAN.md` §22 for the full folder structure. The load-bearing boundary:
`domain/` (scoring, dedupe, grounding, review) is pure — no I/O, no imports from `providers/` or
`repositories/`. That's what keeps the four modules that must be *right* unit-testable in
milliseconds, and it's the answer to "can I use your scorer in a batch pipeline?": `import`.
