# Groundwork — Session Bootstrap

**Before making any change in this repo, read, in order:**
1. `docs/IMPLEMENTATION_PLAN.md` — the full approved plan (v2). This is the source of truth for
   scope, architecture, and rationale. Do not re-derive decisions it already made.
2. `docs/ARCHITECTURE.md` — the condensed map: diagram, the three core claims, the
   deterministic-vs-LLM split, the isolation model, founder discussion points.
3. `docs/PROGRESS.md` — living state: what's done, current checkpoint, tests verified, known
   issues, next task, and what not to touch. This is what lets a fresh session with no conversation
   history pick up correctly.

If any instruction you're given conflicts with these documents, flag the conflict explicitly rather
than silently resolving it — these documents represent an already-negotiated agreement between the
user and a prior session.

---

## Invariants — do not violate without the user's explicit approval

These are the architectural decisions the whole project is built to defend in a founder interview.
Changing any of them is a scope decision, not an implementation detail — surface it, don't silently
drift from it.

- **The orchestrator is deterministic code, not an LLM.** The pipeline DAG is fixed; nothing asks a
  model to plan or re-plan it.
- **ICP scoring is a deterministic weighted rubric.** The LLM may write the prose explanation from
  the computed numbers; it never produces or adjusts the score itself.
- **`ProspectContext` is the isolation boundary.** Every prospect's mutable state — facts, evidence,
  signals, score, contact, drafts — lives only on its own context. No shared dict, no cross-prospect
  reads.
- **Fan-out uses `asyncio.gather(*tasks, return_exceptions=True)`, not `asyncio.TaskGroup`.** One
  prospect failing must never cancel the others. This is a deliberate rejection of structured
  concurrency, not an oversight.
- **Concurrency is bounded** — a global semaphore over prospects (default 3) plus per-provider
  semaphores. Never unbounded fan-out.
- **Evidence is first-class and typed by origin** (`DEMO_FIXTURE` / `LIVE_FETCH` / `LLM_INFERENCE`).
  Synthetic evidence must never carry a real-looking `source_url` — enforce this with a schema-level
  validator, not a UI convention.
- **The review verdict has no LLM in its path.** Seven deterministic checks decide PASS /
  NEEDS_REVIEW / FAIL. An LLM may add advisory notes (P1+) but never the verdict.
- **Demo Mode and Live Mode share the same code path**, differing only in which `LLMProvider` /
  `SearchProvider` implementation is wired in. Never special-case "if demo mode" logic inside the
  engine, steps, or domain layer — only inside the provider implementations themselves.
- **SQLite in WAL mode**, not Postgres, for this prototype. Don't introduce Docker or a second
  database without the user asking.
- **Progress is an append-only event log (`run_events`) replayed over SSE**, not an in-memory
  pub/sub broadcaster. `after_seq` must stay a resumable cursor.
- **`domain/` (scoring, dedupe, grounding, review) is pure.** No imports from `providers/` or
  `repositories/`, no I/O. If you find yourself importing a repository into `domain/`, stop — that's
  the invariant breaking.

---

## Commands

- `make dev` — starts the API (`:8000`) and web app (`:3000`) together.
- `make api` / `make web` — start one side only.
- `make test` — runs the backend test suite (`cd apps/api && uv run pytest`).
- `make seed` — populates the demo fixture pack. **Not implemented until Checkpoint B.**
- `make demo-reset` — wipes the local SQLite DB and reseeds. **Not implemented until Checkpoint B.**

---

## Checkpointed delivery — stop at every boundary

This project is built in six checkpoints (A–F), defined in `docs/IMPLEMENTATION_PLAN.md` §30, each
with a hard-stop time budget and explicit acceptance criteria (§31). **Implement exactly one
checkpoint per session unless the user explicitly authorizes more.** At the end of a checkpoint:

1. Run the checkpoint's verification (tests, curl checks, or manual walkthrough as specified).
2. Update `docs/PROGRESS.md` — completed checkpoints, current checkpoint, tests verified, known
   issues/deviations, next task, and what not to touch.
3. Commit the work with a message naming the checkpoint.
4. Push to the current branch.
5. Report: files changed, commands run, verification results, commit reference, any issues.
6. **Stop.** Do not roll into the next checkpoint without the user asking for it.

This is what lets the user swap Claude Code sessions or models between checkpoints and verify
progress from the documents alone, independent of conversation history.

---

## Scope discipline

Do not add product features, UI polish, or "while I'm here" improvements beyond what the current
checkpoint explicitly calls for — see the P0/P1/P2 split in `docs/IMPLEMENTATION_PLAN.md` §5 and the
cut ladder in §34. If something looks missing but isn't in the current checkpoint's file list, it's
either a later checkpoint or explicitly P1/P2 — check before building it.
