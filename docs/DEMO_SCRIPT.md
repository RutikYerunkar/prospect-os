# Groundwork — Demo Script

Rehearsed twice from a clean `make demo-reset` (see `docs/PROGRESS.md`, Checkpoint F). The numbers
below are the actual, reproducible output of the canonical run — created through the real New Play UI,
not curl — at `seed=42`, the default. If you re-run it, you'll see the same numbers.

**Before you start:** `make demo-reset && make dev`. Open `http://localhost:3000` (redirects to New
Play). Two browser tabs help: one for the walkthrough, one pre-navigated to the run you'll open second.

---

## Full script (5–6 minutes)

### 1. Frame it (20s)

> "This is Groundwork. It turns a growth objective into evidence-backed, scored prospects with drafted
> outreach — and it will not take an external action without a human. Everything you're about to see is
> computed by the same engine in demo and live mode; only the provider layer changes. Demo Mode here
> runs on a deterministic fixture pack — no outbound network calls at all — so the interesting question
> isn't 'is the data real,' it's 'is the engine real.' It is."

### 2. New Play (20s)

Leave the objective as its default: *"Find AI infrastructure startups that recently raised funding or
are expanding their GTM teams. Identify the most relevant sales leader, score each company against our
ICP, explain the evidence, and draft personalized outreach."* Point at the parsed `PlaySpec` rendering
read-only beside the form as you type — industries, size band, prospect count, thresholds.

### 3. Run Agents (10s)

Click **Run Agents**. Navigation to the run happens immediately, before a single prospect has finished —
that's the 202-not-200 API contract, not a spinner hiding latency.

### 4. Visible concurrency (30s)

Seven prospect rows advance **independently and at different rates**. Point at "Agents active — N / 3":
"Three at a time — that's a semaphore (`asyncio.Semaphore(3)`), not a coincidence."

### 5. Retry + independent failure (30s)

Point at Northwind Labs' `↻ retried` badge: "That provider failed on attempt one and succeeded on
attempt two — the retry is in the execution trace, not hidden." Point at Quarry Systems heading to
`FAILED`: "That one's about to exhaust three retries and fail outright — and it will not take the run
down with it." **Refresh the browser mid-run** — state is intact, the SSE stream resumes from its
`after_seq` cursor. A deliberate ten-second flex.

### 6. The PASS prospect — score arithmetic (60s)

Open **Northwind Labs** (score **92**). Walk the score breakdown table: eight dimensions, each with raw
value, weight, and contribution, summing to **92.4 → 92**. *"You could ask why this is 92 and not 75.
Here's the arithmetic. An LLM did not pick this number — it wrote the one-sentence explanation
underneath it, from this table, after the fact."* Point out `industry_fit` and `size_fit` read
**"supported · profile"** rather than an evidence count — those two come straight from the company's
structural record, not a citable claim, and the UI says so instead of showing a confusing "0".

### 7. Evidence provenance (30s)

Scroll to the evidence cards. Every one carries a **SYNTHETIC** badge and "Synthetic evidence · demo
fixture" caption, with no clickable link. *"These are labeled synthetic on purpose — I'm not going to
show you a fake TechCrunch link. The schema itself forbids a `source_url` on anything but a real live
fetch; it's not a UI convention I could forget to apply."*

### 8. Grounded outreach (30s)

Scroll to Outreach. Point at the claim references under the draft: *"Every sentence that cites a fact
resolves to a specific evidence row. If it can't, it's flagged unsupported — that's what the review
step downstream actually checks."*

### 9. Deterministic review checks (30s)

Scroll to Review & Guardrails. All seven checks, `PASS`, with reasons. *"No LLM anywhere in this panel.
The model that wrote the draft doesn't get to grade its own homework."*

### 10. Approve (15s)

Click **Approve**. *"That's a state transition into an audit table. Nothing fired externally — there's
no email/CRM provider wired in at all, so there's structurally nothing for this button to trigger."*

### 11. The bad ones (45s)

- **Riverbend Analytics** (`NEEDS_REVIEW`, score 35): its funding claim got demoted — cited evidence
  didn't token-overlap the claim — so it doesn't score, and review's `score_support` check fails soft.
- **Cobalt Retail Systems** (`REJECTED`, score 25): a hard disqualifier — `retail_pos` is on the exclude
  list — caps what would otherwise be a 69-point rubric total at 25. The score breakdown shows both
  numbers, not just the capped one.
- **Northwind Labs Inc.** (`DUPLICATE`): caught on a normalized domain and **shown**, not silently
  dropped — a silent dedupe is indistinguishable from a bug.
- **Ferrous Grid** (`NEEDS_REVIEW`, contact `UNAVAILABLE`): no qualifying buyer was found, so
  personalization was **skipped** — nothing was invented to fill the gap.

### 12. Quality tab (45s)

Click **Quality**. *"I didn't just check that the agents produced output — I check whether it was
supportable."* Point at evidence coverage, grounded-claim rate, dimension support, the guardrail pass
rates with clickable failed-prospect links, per-step reliability, retries, p50/p95 duration. *"Computed
on read from this run's own records, every time you load this tab — there's no metrics table that could
drift from what actually happened."*

### 13. Close — production scaling (45s)

> "At seven prospects this is asyncio in one process against SQLite. At ten thousand it's Postgres,
> distributed rate limiting across providers, and eventually a durable workflow engine — and the seams
> for that migration already exist: every step is idempotent by `(run_id, prospect_id, step_name)`,
> state lives in the database rather than in memory, and providers sit behind Protocols instead of
> being called inline. What breaks first is SQLite's single-writer lock, not the orchestration model —
> the fan-out already works the way a distributed system would need it to."

---

## Two-minute shortened version

If given little time, cut straight to the load-bearing claims:

1. **(15s)** Frame it — evidence-backed, scored, human-approved, no outbound action.
2. **(30s)** Run Agents → point at concurrent rows + the retry badge. "Bounded semaphore, independent
   failure isolation."
3. **(45s)** Open Northwind Labs → score breakdown table (arithmetic, not a model's opinion) →
   evidence cards (SYNTHETIC badge, no fake links).
4. **(20s)** Review panel — seven deterministic checks, no LLM.
5. **(10s)** Quality tab — one glance at guardrail pass rates.
6. **(20s)** Close — "SQLite's write lock is the first ceiling, not the orchestration; the migration
   seams already exist."

---

## Strongest one-liners for the founder's questions

**"Why isn't the orchestrator an agent?"**
"The pipeline is a known, fixed DAG — seven steps, always in the same order. An agent would be choosing
among a known set of options with extra steps and extra failure modes. Variance in *which steps run* is
a cost here, not a feature; the ambiguity that's actually worth an LLM is inside individual steps
(research extraction, personalization), not in deciding what steps exist."

**"Why isn't ICP scoring an LLM?"**
"Because I need to answer 'why 92 and not 75' with a table, not a vibe. The rubric is eight weighted
dimensions and pure arithmetic — reproducible, unit-tested, and tunable by changing a weight, not by
re-prompting. The LLM writes the sentence under the number; it structurally cannot change the number,
because the explanation call only ever receives the already-computed score as read-only context."

**"How do you prevent prospect context leakage?"**
"Structurally, not by convention. Every prospect gets its own `ProspectContext` — the only state its
steps touch — and prompt envelopes are built only from that context, so there's no shared conversation
object to leak into. Then there's a runtime guardrail, `cross_prospect_leak`, that scans every outreach
draft for another prospect's name or domain, on every run, on real data. And there's a test —
`test_isolation.py` — that runs two confusable prospects with unique canary tokens through the real
engine concurrently and asserts zero contamination. It's enforced and it's observable, not just
promised."

**"Why deterministic fixtures?"**
"Because Demo Mode swaps the provider layer, not the engine — every score, status, duplicate flag, and
review verdict on screen is computed live by the same code Live Mode will use. Fixtures give me
evidence, not verdicts: I authored source claims for seven companies, and the engine independently
decided PASS/NEEDS_REVIEW/REJECTED/DUPLICATE/FAILED from that evidence at run time. That's a stronger
demo than curated screenshots, because you can watch it compute in front of you."

**"How do you handle hallucinations?"**
"Two layers. First, grounding: every claim carries evidence IDs, and a deterministic verifier checks the
claim text actually token-overlaps the cited evidence's snippet — an ungrounded claim gets demoted, not
trusted. Second, review: `claim_grounding` and `no_fabricated_contact` are two of seven deterministic
checks with zero LLM involvement, precisely because the model that generated a claim is the worst judge
of whether that claim is true."

**"Why SQLite?"**
"Zero infrastructure for a prototype that needs to be resettable in under a second — `make demo-reset`
is delete-the-file-and-recreate-the-schema. It's WAL mode, so reads don't block writes. It stops being
the right choice around 20–30 concurrent prospects, where the single-writer lock becomes the ceiling —
and that's a known, named tradeoff, not something I'm hoping nobody asks about."

**"What changes at 10,000 prospects?"**
"Postgres first, for concurrent writers. Then distributed rate limiting once more than one process is
calling providers. A durable workflow engine only once run duration makes a mid-deploy interruption
unacceptable — and the seams for that are already here: idempotent steps keyed by
`(run_id, prospect_id, step_name)`, all state in the database instead of in memory, providers behind
Protocols instead of called inline. What breaks first is SQLite's write lock, not the concurrency model
— the `asyncio.gather(return_exceptions=True)` fan-out already behaves the way a distributed queue
consumer would need to."

---

## Notes from rehearsal

- Ran twice from `make demo-reset && make dev`, both times through the real New Play UI (not curl),
  both times producing the identical outcome distribution: **PASS ×2 (Northwind Labs 92, Sable Compute
  79), NEEDS_REVIEW ×2 (Riverbend Analytics 35, Ferrous Grid 58), REJECTED ×1 (Cobalt Retail Systems
  25), DUPLICATE ×1 (Northwind Labs Inc.), FAILED ×1 (Quarry Systems)** — byte-identical across runs at
  the default seed.
- New Play's default target count (7) and default target-industry chip (`ai_infrastructure`, the
  fixture pack's own slug) were adjusted during this checkpoint so the canonical run created through the
  UI reproduces the exact documented reference numbers above — earlier defaults silently diverged
  (wrong prospect count, and a human-readable industry label that only earned a partial adjacent-match
  instead of a full match). Worth knowing this was a real bug found and fixed during rehearsal, not
  always-true.
- No browser console errors (page-level) and no horizontal overflow at 1440×900 across either
  rehearsal. A handful of expected `404` network log lines appear only when *deliberately* navigating to
  an invalid run/prospect id to test the error state — that's the app correctly asking the API and
  handling the 404, not a defect.
- Screenshots from rehearsal are kept locally for reference only (not committed to the repository, per
  scope).
