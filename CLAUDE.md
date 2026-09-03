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

## v2 invariants — Contact Enrichment & Governed Outbound Action

Groundwork v2 (`docs/V2_IMPLEMENTATION_PLAN.md`, frozen Rev 4) extends the pipeline with contact
enrichment and governed outbound action. These invariants are as load-bearing as the v1 list above —
any fresh session picking up v2 work must preserve them without re-deriving or re-litigating them:

- The five contact axes (person identity, email discovery, email verification, LinkedIn resolution,
  LinkedIn identity match) are independent — never collapsed into one flag.
- Enrichment never writes `Contact.verification` — that column is the person-identity axis only, and
  writing to it from enrichment would move every ICP score in the canonical demo.
- No LLM-authored identifiers, anywhere. An email address or LinkedIn URL reaches the system only from a
  provider observation row.
- No LLM identity matching. LinkedIn identity matching is deterministic, versioned, string-based
  matching in `domain/contact_identity.py` — no fuzzy matching, no edit distance.
- Provider observations are not verdicts — `domain/` derives states from them, pure and offline;
  `domain/` never contains a provider's name (e.g. never the string `"apollo"`).
- Origin determines legal identifier grammar. A `DEMO_FIXTURE` observation may carry only `demo://…`
  identifiers; a `LIVE_PROVIDER` observation may carry only validated real ones — enforced twice (model
  validator + pure derivation), never inferred.
- Demo identifiers are synthetic and must never be real-looking external URLs or addresses — the same
  `Evidence._no_fake_sources` discipline applied to two new identifier classes.
- An approval binds channel + sender + recipient + subject + body through a versioned content hash. Any
  change to any of those fields voids the approval.
- An `ACTION`-scope approval carries `action_proposal_id`, `content_hash`, **and** `hash_version`
  together — never just the hash.
- `VERIFIED` is the only sendable email verification state.
- `PASS` (the review verdict) is the send hard floor for `EMAIL_SEND`.
- No override path exists anywhere in v2. A blocked action shows why and offers no button.
- No `LINKEDIN_SEND` executor exists. `ActionType` has exactly two members
  (`EMAIL_SEND`, `LINKEDIN_COPY_AND_OPEN`); LinkedIn action is copy-and-open only.
- Post-dispatch ambiguity becomes `UNCERTAIN`, never a guess in either direction.
- Never automatically resend an ambiguous (`UNCERTAIN`) send.
- Request idempotency applies in both Demo and Live — the same approved execution can never run twice in
  either mode.
- **One initial `LIVE_EXTERNAL` email per normalized recipient identity, across runs.** This recipient-
  level rule is `LIVE_EXTERNAL`-only — it does not apply to, and is never checked against, Demo rows.
- `DEMO_SIMULATED` executions never reserve or consume the live recipient identity, and a prior Demo
  execution never blocks a later Live send to the same address.
- `sender_identifier` is canonicalized (via `normalize_email_identity`) before it is persisted on a
  proposal — computed once, at proposal creation, never re-derived downstream.
- Live execution remains operator-gated — a valid operator session is one of five required server-side
  gates, none of which is the UI.
- Public Demo execution is zero-egress — `DemoEmailSendProvider` opens no socket and performs no DNS
  lookup, by construction, not by convention.
- No fixture fallback in Live, ever. A missing key or disabled enrichment degrades honestly
  (`NOT_ATTEMPTED` / `422`); it never silently substitutes Demo data.
- Zero paid provider calls in CI. Every smoke script (Apollo, Gmail) is gated by
  `--i-understand-this-costs-money` and a configured key, and is never invoked by `make test`.
- Checkpoint PRs target `feature/v2-contact-enrichment`, never `master`.
- `master` remains untouched until the single V2-J integration PR; Render keeps deploying `master` only.

**Note on `ActionExecutionOrigin.LIVE_EXTERNAL`:** it means execution on the live external-action
path — capable of a real external side effect. It is **not** proof that a message actually left the
system or was delivered. Delivery outcome is represented separately, by execution status/outcome
(`SUCCEEDED` / `FAILED` / `UNCERTAIN` / `ABANDONED`). Do not conflate origin with outcome.

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
