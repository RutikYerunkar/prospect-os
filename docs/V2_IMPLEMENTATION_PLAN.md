# Groundwork v2 — Contact Enrichment & Governed Outbound Action

**Status:** approved architecture (**rev 4 — frozen**). This document persists that frozen architecture
into repository documentation at Checkpoint V2-A. **V2-A itself changes no application code, writes no
migration, and makes no provider call.**

This is a new document, not a rewrite. `docs/IMPLEMENTATION_PLAN.md` remains the historical v1 record
and is not edited by v2 — see "Relationship to `IMPLEMENTATION_PLAN.md`" below. Where this document says
"unchanged from v1" it means precisely that: the referenced v1 code, table, or invariant is reused
as-is.

**Revision history**

- **Rev 4** (this pass): recipient-level duplicate-send protection scoped to **LIVE external sends
  only**, so public Demo executions can never consume the global recipient identity (§3.5B, D10, D14);
  `approvals.hash_version` added to the additive schema with a structural CHECK and an execute-time
  supersede gate (Part 5, §3.9, policy clause 9); `sender_identifier` canonicalization pinned at every
  persistence layer (§3.10). Everything else from rev 3 preserved verbatim.
- **Rev 3**: origin-aware LinkedIn identifier grammar resolving the Demo contradiction (§3.7);
  `sender_identifier` bound into the proposal and the hash (§3.9, Part 5, Part 6.1); the normative
  `HASH_VERSION` algorithm restored (§3.9); the cross-run duplicate-send hole closed with a
  recipient-centric safety identity enforced by a partial unique index (§3.5, §3.8). Everything else
  from rev 2 preserved verbatim — see Part 16 for the explicit preserved-invariants checklist.
- **Rev 2**: least-privilege Gmail reconciliation; resend contradiction removed; last-known-good
  enrichment; deterministic identity matching; tightened failure taxonomy; checkpoint branch/PR
  isolation; Demo action gating decided as Option B.

**User decisions carried forward:** `PASS` is a hard floor · `VERIFIED` is the only sendable email
state, **no override anywhere** · v2 starts now, the I2 backlog folds into V2-J.

---

## Context

Groundwork v1 is production-stable on `master` (tag `v1.0.0-production`), deployed on Render against
Neon. It runs `research → qualify → identify contact → draft → review → human approval` and stops. v2
extends that to `… → enrich contact → verify professional email → resolve LinkedIn → channel-specific
outreach → deterministic review → human approval → execute allowed channel action → immutable audit
trail`, with Apollo as the first enrichment provider and Gmail as the only execution provider.

`docs/IMPLEMENTATION_PLAN.md` §3 excludes outbound sending and commercial data providers; §5 lists
"Sending infra" under **P2 — talk about, don't build**. But its Addendum already names the sanctioned
shape: integrations behind the same Protocol pattern, with the approval boundary as where side effects
hang. The accurate framing is **a deliberate P2 → P0 promotion, not an architectural reversal** — v2
does not relax `IMPLEMENTATION_PLAN.md` §3's "No fabricated contact data, ever"; it extends that same
rule to two new identifier classes (email, LinkedIn) under the same structural-validator discipline
`Evidence._no_fake_sources` already established for evidence.

### Relationship to `IMPLEMENTATION_PLAN.md`

House convention is settled: `IMPLEMENTATION_PLAN.md` is a historical record and is not retrofitted
(its own Addendum). **v2 does not edit `IMPLEMENTATION_PLAN.md` §3, §5, or its Addendum.** This document
is the v2-equivalent plan, living beside it. `docs/PROGRESS.md` marks I2 with an open next-task list;
those items are absorbed into V2-J rather than dropped or blocking (see Part 13).

---

## Part 1 — Architecture assessment

### 1.1 What v1 gives us for free — reuse, do not rebuild

| v1 asset | v2 reuse |
|---|---|
| `providers/base.py` Protocols + `*AttemptTelemetry` + typed error hierarchy | Copy the shape for `EnrichmentProvider` / `EmailSendProvider`. Four provider families, one idiom. |
| `engine/llm.py::call_structured`, `engine/search.py::call_search` | `engine/enrichment.py::call_enrichment` — the *only* place enrichment telemetry is persisted. Providers keep never importing repositories (`test_provider_purity.py`, AST inspection). |
| `llm_calls` / `search_calls`, `UNIQUE(call_group_id, attempt)`, one flat retry loop, one outbound call site | `enrichment_calls` / `action_send_calls` follow it exactly. No nesting, no `tenacity`. |
| `engine/search_budget.py::SearchCallBudget` — atomic reserve-before-call under one lock | `EnrichmentCallBudget` and `ReconcileCallBudget`. Same single check-and-increment. |
| `runs.executor_id` + guarded `UPDATE … WHERE executor_id = :x AND status = …` | The send-dispatch claim and the last-known-good channel update. |
| **`Evidence._no_fake_sources`** — origin decides which identifier shape is structurally legal | The direct precedent for the origin-aware LinkedIn grammar (§3.7) and for the demo sender/message-id validators. |
| `approvals` — append-only, carries `actor`, never overwrites engine status | Extend additively. Every v1 row stays a valid `PROSPECT`-scope approval. |
| Operator session, `enforce_live_gate`, `require_allowed_origin` (Origin-header CSRF) | The authorization spine. No new auth system. |
| `domain/psl.py` — offline registrable-domain normalization (pinned `tldextract`, `suffix_list_urls=()`) | Company-domain equality in identity matching, and the LinkedIn host check. |
| `domain/url_safety.py` | Validating a LIVE LinkedIn profile URL before it can ever be a `RESOLVED` identifier. |
| `observability/redact.py` single choke point + the second redaction at the logging boundary | Add an email-address rule to the existing choke point; never a second path. |
| Alembic-for-Postgres / `create_all`-for-SQLite split + `test_migration_drift.py` | v2 schema lands in `models/tables.py` plus one additive revision. |
| **`EvidenceCard.tsx`'s origin gate** (`origin === "LIVE_FETCH" && source_url` before rendering a link) | The direct precedent for never emitting a `demo://` identifier as an external hyperlink (§3.7, Part 7). |
| `/evaluation` computed-on-read, generic `reason: count` aggregation | Every v2 metric. No metrics table. |
| Fixture pack = evidence, never verdicts | The v2 enrichment fixture format inherits the rule verbatim. |
| `--i-understand-this-costs-money` + configured-key + never-in-CI smoke convention | Any Apollo or Gmail smoke script copies it. |
| `OutreachDraft.channel` and `step_index` already exist | Multi-channel anticipated; `step_index` is the named carrier for a future sequence identity. |

### 1.2 Conflicts and hazards

| # | Issue | Resolution |
|---|---|---|
| C1 | §3/§5 exclude outbound sending and commercial data providers; the Addendum names the slot. | P2 → P0 promotion, recorded in this document's changelog. Do not edit the v1 plan. |
| C2 | `no_fabricated_contact` (HARD) is dormant today — nothing writes `Contact.email`/`linkedin_url`. It goes live the instant Apollo fills them, and as written hard-`FAIL`s any contact with an email that is not `VERIFIED`. | Keep `ContactVerification` as the *person-identity* axis only; put reachability on independent axes; rewrite the check to be per-axis and provenance-based. Still exactly seven checks (Part 6). |
| C3 | `persona_availability` is a scored dimension at weight 0.10 driven by `ContactVerification`. Feeding enrichment into it would move every ICP score in the canonical demo. | **Invariant: enrichment output never writes `Contact.verification`.** Reachability lives on `contact_channels` and touches no scoring input. |
| C4 | **Name collision.** `engine/steps/enrich.py` already means the deterministic field-precedence merge. | Name the new step `contact_enrichment`. Never reuse the word `enrich` for it, in code, events, or docs. |
| C5 | `OutreachDraft.subject` is `NOT NULL`; `_no_placeholders` hard-fails an empty subject; a LinkedIn message has no subject. | `subject` → nullable (`DROP NOT NULL`, no rewrite); the subject-required clause becomes channel-conditional inside the same check. |
| C6 | `personalize` produces one draft and is skipped wholesale when `verification == UNAVAILABLE`. | Emit one draft per eligible channel. Canonical email drafts stay byte-identical; LinkedIn drafts additive. |
| C7 | The canonical demo board (`PASS:2 NEEDS_REVIEW:2 REJECTED:1 DUPLICATE:1 FAILED:1`, seed 42, `PARTIAL`, 3 retries) is re-verified byte-identical at every phase gate since Checkpoint B. | v2 must not change any prospect's status, score, review verdict, or evidence count. `target_count` stays 7. |
| C8 | `run_events` is run-scoped with a per-run `seq` SSE cursor; the stream closes when the run finishes. Actions happen *after*. | Actions get their own append-only `action_events` table; the UI refetches the aggregate. |
| C9 | `proxyHeaders.ts::FORWARDED_REQUEST_HEADERS` is a 4-entry allow-list. | **No new request header** — no `Idempotency-Key` header. Idempotency inputs travel in the JSON body. Zero proxy changes. |
| C10 | `useRunStream.ts` registers listeners from an explicit `KNOWN_EVENT_TYPES` array; unknown SSE types are silently dropped. | Any new run-scoped event type must be added there in the same checkpoint that emits it. |
| C11 | `ContactRow` has no uniqueness constraint; `get_contact` orders by `id DESC`. | Do not fix opportunistically. `contact_channels` gets `UNIQUE(prospect_id, channel)` from the start. Note as a known adjacent issue. |
| C12 | `domain/discovery.py::STRUCTURAL_AGGREGATOR_DOMAINS` blocks `linkedin.com` — but that is about *company domain identity*, not people. | Leave it alone. Contact LinkedIn URLs never flow through domain resolution. Code comment so nobody "fixes" one by breaking the other. |
| C13 | `step.failed` documented but never emitted; `plan.created` never emitted. | Pre-existing gaps. v2 neither papers over them nor depends on them. |
| C14 | The session instruction said to develop directly on `feature/v2-contact-enrichment`. | The user has granted and specified per-checkpoint branches (Part 12). |
| C15 *(rev 3)* | A strict `linkedin.com` + `/in/…` URL gate would deterministically reject the canonical Demo identifier `demo://linkedin/priya-natarajan`, forcing Northwind to `NOT_FOUND` — contradicting the Demo matrix, which requires `RESOLVED` + `STRONG_MATCH`. | **Origin-aware identifier grammar** (§3.7 Step 0): two mutually exclusive shapes selected by the observation's `origin`, enforced twice (model validator + pure derivation), never inferred. |
| C16 *(rev 3)* | The proposal bound channel/recipient/subject/body but **not the sending account**. Gmail could be disconnected and reconnected to a different identity after approval, and the approval would still authorize the send. | `sender_identifier` on the immutable proposal and inside the hash; freshly re-resolved and compared at execute time (§3.9, Part 6.1). |
| C17 *(rev 3)* | D10's "one send per (prospect, channel)" used a run-scoped identity. The same person rediscovered in a later run is a different `prospect_id`, so they could receive a second initial email. | **Recipient-centric safety identity** (§3.5, §3.8): a normalized email identity key with a partial unique index across the whole table, independent of run or prospect. |

---

## Part 2 — Major design decisions (D1–D14)

**D1 — Five independent state axes, never one flag.** Person identity, email discovery, email
verification, LinkedIn resolution, LinkedIn identity match.

**D2 — Providers return *observations*; `domain/` derives *states*.** `domain/` never contains the
string `"apollo"`.

**D3 — An LLM can never emit an identifier**, and never performs identity matching. Identifiers reach
the system only from a provider row.

**D4 — Approval authorizes an exact outbound action, not a prospect.** *(Extended in rev 3.)* The hash
covers channel, sender, recipient, subject and body. Any change to any of them voids the approval.

**D5 — Ambiguity is a first-class state.** Acceptance that cannot be proven is `UNCERTAIN`, reconciled
against evidence, and stays `UNCERTAIN` if the evidence is inconclusive.

**D6 — LinkedIn has no executor.** `ActionType` has no `LINKEDIN_SEND` member.

**D7 — There is no override path anywhere in v2.** A blocked action shows *why* and offers no button.

**D8 — Demo simulated execution is public; Live execution is strictly operator-gated.** (Part 14.)

**D9 — Origin decides which identifier shapes are structurally legal.** *(Generalized in rev 3.)* A
`DEMO_FIXTURE` observation may carry only `demo://…` identifiers; a `LIVE_PROVIDER` observation may
carry only validated real ones. A demo execution's `provider_message_id` must start with `demo://`; a
demo proposal's `sender_identifier` must be `@groundwork.invalid`. Enforced by model validators *and* by
the pure derivation, twice, exactly like `Evidence._no_fake_sources`.

**D10 — One initial real email per recipient identity, forever.** *(Corrected in rev 3; scoped in
rev 4.)* The safety identity is the normalized recipient address, **not** `(prospect_id, channel)`. It
governs **`LIVE_EXTERNAL` executions only** — a simulated Demo send is not a message to a human and must
never consume it (D14). No resend, no follow-ups in v2.

**D11 — Least privilege beats convenience on OAuth scope.** Design inside `gmail.metadata`; escalate
only with evidence, at a documented gate.

**D12 — Request idempotency and recipient-level send policy are two different mechanisms.** *(Rev 3.)*
One stops the same approved execution running twice; the other stops two different approvals sending two
initial emails to the same human. They are enforced separately and tested separately.

**D13 — The sending identity is an identifier, never a credential.** *(Rev 3.)* `sender_identifier` is
the connected Google account's email address. No token, refresh token, client secret or scope string
ever enters a proposal, a hash, or an audit payload. *(Rev 4: it is stored canonically at every
layer — §3.10.)*

**D14 — Execution origin decides which safety rules bind.** *(Rev 4.)* `ActionExecutionOrigin` is
`DEMO_SIMULATED` (zero network egress, `demo://` message id, `@groundwork.invalid` sender) or
`LIVE_EXTERNAL` (execution on the live external-action path — capable of real external side effects;
**this is not itself proof that a message left the system or was delivered**, which is represented
separately by execution status/outcome — `SUCCEEDED` / `FAILED` / `UNCERTAIN` / etc., §3.2/§3.4).
**Request idempotency binds in both.** The recipient-level cross-run rule binds **only
`LIVE_EXTERNAL`** — otherwise the first portfolio visitor to simulate the Northwind send would
permanently block every later visitor with `already_sent_to_recipient`, and a demo row would poison live
deduplication. A demo execution never consumes or reserves a live recipient identity, and never blocks a
future live send; a live execution can never bypass the rule. Origin is bound by run mode, exactly like
the provider itself.

---

## Part 3 — Domain model & state machines

### 3.1 The five contact axes

`ContactVerification` (v1, **unchanged enum, column and derivation**) *is* the person-identity axis:
`VERIFIED` = a named person grounded in evidence · `PERSONA_ONLY` = a role, no name · `UNAVAILABLE` =
neither. Nothing about reachability. Per C3, writing to it would move every ICP score.

```python
class EmailDiscoveryState(StrEnum):
    NOT_ATTEMPTED  = "NOT_ATTEMPTED"    # enrichment disabled, or no named person to look up
    NOT_FOUND      = "NOT_FOUND"        # a provider call SUCCEEDED and returned no address
    FOUND          = "FOUND"
    PROVIDER_ERROR = "PROVIDER_ERROR"   # no successful observation has ever been obtained (§3.6)

class EmailVerificationState(StrEnum):
    UNVERIFIED   = "UNVERIFIED"         # no signal; also the fail-closed default for unmapped statuses
    UNVERIFIABLE = "UNVERIFIABLE"
    RISKY        = "RISKY"              # catch-all domain / low provider confidence
    VERIFIED     = "VERIFIED"           # the ONLY sendable state
    INVALID      = "INVALID"

class LinkedInResolutionState(StrEnum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"; NOT_FOUND = "NOT_FOUND"
    RESOLVED = "RESOLVED";           PROVIDER_ERROR = "PROVIDER_ERROR"

class LinkedInIdentityState(StrEnum):
    UNKNOWN = "UNKNOWN"; MISMATCH = "MISMATCH"
    WEAK_MATCH = "WEAK_MATCH"; STRONG_MATCH = "STRONG_MATCH"   # only STRONG is actionable
```

### 3.2 Action state machine

```
OutreachDraft(channel, subject?, body)
        │  proposal built from draft + resolved channel identifier + resolved SENDER identity
        ▼
  ActionProposal(sender_identifier, recipient_identifier, content_hash)   [immutable]
        │
        ├── domain/action_policy.evaluate() ──► BLOCKED  (reasons recorded; no override — D7)
        └──────────────────► ELIGIBLE
                                │  operator approves (Approval binds proposal_id + content_hash)
                                ▼
                            APPROVED ── sender/recipient/subject/body changes ──► SUPERSEDED
                                │  POST /actions/{id}/execute
                                │  re-verify: capability → approval → hash → LIVE SENDER MATCH → policy
                                ▼
                        ActionExecution
```

```
        INSERT (UNIQUE idempotency_key)  +  (partial UNIQUE on recipient_identity_key)
              ┌───▼─────┐
              │ CLAIMED │  committed BEFORE any network call, carrying our Message-ID
              └───┬─────┘
    guarded UPDATE … WHERE status='CLAIMED' AND executor_id=:me
              ┌───▼──────┐
              │ IN_FLIGHT│   dispatched_at set the moment the body hits the transport
              └───┬──────┘
                  │   outcome classified by §3.4
   ┌──────────────┼───────────────────────────┬──────────────────────┐
   ▼              ▼                           ▼                      ▼
SUCCEEDED   PROVEN_NOT_DISPATCHED    DEFINITIVE_REJECTION    ACCEPTANCE_UNKNOWN
             └────────┬─────────────────────┘                        │
                      ▼                                              ▼
                   FAILED                                       UNCERTAIN
     (the ONLY state that frees the recipient identity)              │
                                            bounded reconciliation (§3.3)
                                    ┌──────────────┬─────────────────┐
                                    ▼              ▼                 ▼
                                SUCCEEDED   NOT_FOUND_WITHIN_BOUNDS  LOOKUP_FAILED
                                                   └────► UNCERTAIN (stays) ◄────┘
                                                            │ operator marks ABANDONED (+reason)
                                                            └─ does NOT free the recipient identity
```

Terminal: `SUCCEEDED`, `FAILED`, `ABANDONED`. A process dying between `CLAIMED` and a settled state is
recovered by a stale-claim sweep to `UNCERTAIN` — never to `FAILED`, never re-dispatched.

### 3.3 Reconciliation under least privilege

Google documents that the `q` parameter is not usable with the `gmail.metadata` scope. Broadening to
`gmail.readonly` to make a query work would be exactly the reflex this project should not have. We do
not need search; we need to answer "is a message carrying our `Message-ID` among the handful this
account has sent since we dispatched?" — a bounded scan.

```
reconcile(execution):
    1. users.messages.list(userId="me", labelIds=["SENT"], maxResults=PAGE_SIZE)
       — labelIds, NOT q. Returns ids newest-first.
    2. for each id, newest first:
           users.messages.get(userId="me", id=id, format="metadata",
                              metadataHeaders=["Message-ID", "Date"])
       compare to execution.message_id_header (case-insensitive, angle-brackets normalized)
    3. STOP on:  match                                        -> FOUND(provider_message_id=id)
                 internalDate < dispatched_at - CLOCK_SKEW    -> NOT_FOUND_WITHIN_BOUNDS
                 messages_scanned >= RECONCILE_MAX_MESSAGES   -> NOT_FOUND_WITHIN_BOUNDS
                 pages >= RECONCILE_MAX_PAGES                 -> NOT_FOUND_WITHIN_BOUNDS
```

`internalDate` arrives on the same `messages.get` we already need, so "stop once we have scanned past
our own dispatch time" costs nothing and is the real bound; the caps are a backstop.

| Bound | Default | Rationale |
|---|---|---|
| `RECONCILE_PAGE_SIZE` | 25 | one `messages.list` page |
| `RECONCILE_MAX_PAGES` | 2 | ≤50 ids considered |
| `RECONCILE_MAX_MESSAGES` | 50 | hard cap on `messages.get` calls per attempt |
| `RECONCILE_CLOCK_SKEW_S` | 60 | tolerance on `internalDate` vs `dispatched_at` |
| `RECONCILE_MAX_ATTEMPTS` | 3 | ~+30s, +2min, +10min after dispatch |
| `RECONCILE_WINDOW_S` | 900 | after 15 min, stop attempting; stay `UNCERTAIN` |

Worst case 3 × (1 list + 50 gets) = 153 metadata calls at 5 quota units each against 250 units/user/sec,
spread over 15 minutes — immaterial. Every call is recorded in `action_send_calls` with
`operation=reconcile` and charged against an atomic `ReconcileCallBudget` in the `SearchCallBudget`
style.

**What this cannot do, stated honestly.** It relies on a sent message appearing in `SENT` and being
retrievable by metadata within the window. If indexing lag exceeds `RECONCILE_WINDOW_S`, or if
`labelIds` turns out to be restricted under `gmail.metadata` too, this returns `NOT_FOUND_WITHIN_BOUNDS`
— which is **not** evidence of non-delivery and must never be treated as such.

> **V2-G hard gate:** verify, against live Google documentation and a real consented test account,
> whether `messages.list(labelIds=["SENT"])` and `messages.get(format="metadata")` are permitted under
> `gmail.metadata`. Record the finding in `PROGRESS.md` the way Tavily's `include_usage` shape was.
> **V2-I hard gate:** if metadata-only reconciliation is not viable, present the `gmail.readonly` trade
> to the user as an explicit decision — never take it silently. Until then ship `gmail.send` +
> `gmail.metadata` only, and let unreconcilable sends stay `UNCERTAIN`.

A provider that cannot reconcile sets `supports_message_id_lookup = False`; for it `UNCERTAIN` is
permanently terminal, which the recipient-level rule then treats as blocking.

### 3.4 Send failure taxonomy

`dispatched_at` is the classification boundary — set the moment the request body is handed to the
transport. **A 5xx is not evidence of non-delivery.**

| Class | Includes | Execution state |
|---|---|---|
| `PROVEN_NOT_DISPATCHED` | pre-flight validation failure; any exception before the request was written; DNS failure; TCP connect refused/unreachable; TLS handshake failure; a token-refresh failure on the *preceding* call | **FAILED** |
| `DEFINITIVE_REJECTION` | a parsed response on the send call whose semantics are unambiguously "not accepted": `400` malformed, `403` insufficient permission, `404`, `429` rate-limited (rejected, not queued — distinct `error_type`) | **FAILED** |
| `ACCEPTANCE_UNKNOWN` | read timeout after the body was written; connection reset mid-response; any client deadline firing after dispatch; **any 5xx**; any unparseable response; anything not positively classified above | **UNCERTAIN** |

Classification is an allow-list of provable outcomes, not a denylist. Never resend after a request that
reached the wire. Exact Gmail semantics for specific 5xx codes are confirmed at V2-I against documented
behaviour and recorded with a citation — never moved by inference.

### 3.5 Two distinct duplicate-prevention mechanisms *(rev 3 — corrected)*

Rev 2 conflated these. They answer different questions and are enforced by different constraints.

#### (A) Request idempotency — the same approved execution cannot happen twice · **BOTH modes**

```python
idempotency_key = sha256(f"{approval_id}|{content_hash}".encode()).hexdigest()
```

`action_executions.idempotency_key` carries a plain, non-partial `UNIQUE` constraint, so it binds
identically for `DEMO_SIMULATED` and `LIVE_EXTERNAL`. A duplicate execute request (double-click, retried
HTTP request, impatient refresh) loses the insert race and returns the existing execution's current
state rather than creating a second one. `approval_id` alone would be enough, but including
`content_hash` makes the key self-describing in the audit trail.

Because the key is derived from `approval_id`, two different demo visitors running the canonical demo
from scratch produce two different approvals and therefore two different keys — each completes its own
simulated send, which is exactly the public-portfolio behaviour Part 14 requires. One approval,
double-clicked, still yields exactly one execution.

Write-ahead ordering is unchanged: the row commits as `CLAIMED` carrying our generated RFC-5322
`Message-ID` before the provider is called, so a crash mid-call leaves durable evidence that a send may
exist.

#### (B) Recipient-level send policy — two different proposals cannot both send an initial real email to the same human · **LIVE ONLY**

Rev 2's identity was `(prospect_id, channel)`. `prospect_id` is run-scoped, so the same person
rediscovered in a later run is a different prospect and would have sailed through. Rev 3 fixed the
identity but scoped the index to the whole table — which introduced a second, worse bug: public Demo
executions write `action_executions` rows carrying the canonical fixture recipients, so the first
portfolio visitor to simulate the Northwind send would have permanently blocked every later visitor, and
that demo row would also have blocked a genuine live send to the same address. **Rev 4 corrects the
scope to `LIVE_EXTERNAL` only.**

```python
action_executions.recipient_identity_key   # normalize_email_identity(recipient), §3.8
                                           # ALWAYS populated for EMAIL_SEND, in BOTH origins,
                                           #   so demo rows stay auditable and countable;
                                           # NULL for LINKEDIN_COPY_AND_OPEN — not a send
action_executions.origin                   # ActionExecutionOrigin: DEMO_SIMULATED | LIVE_EXTERNAL
```

The rule is a database constraint, not application logic — and its predicate names all three conditions
explicitly so the index itself documents what it protects:

```sql
CREATE UNIQUE INDEX uq_action_executions_live_recipient
    ON action_executions (action_type, recipient_identity_key)
 WHERE origin      = 'LIVE_EXTERNAL'
   AND action_type = 'EMAIL_SEND'
   AND status IN ('CLAIMED', 'IN_FLIGHT', 'SUCCEEDED', 'UNCERTAIN', 'ABANDONED');
```

- **`origin = 'LIVE_EXTERNAL'` is the rev-4 correction.** A `DEMO_SIMULATED` row is not a message to a
  human; it neither consumes nor reserves a live recipient identity. A demo and a live row for the same
  address may coexist — they are different facts.
- `FAILED` is deliberately excluded from the status set, and is the only state that frees a recipient
  identity — justified because §3.4 defines `FAILED` as *provably* non-delivered. Everything unproven is
  `UNCERTAIN`, which blocks.
- `ABANDONED` still blocks. Marking an `UNCERTAIN` execution abandoned records an operator's judgement
  for the audit trail; it does not license a second attempt at that human.
- Within `LIVE_EXTERNAL`, the scope is the whole table — every run, every prospect, every play.
  `prospect_id` and `run_id` stay on the row for provenance and audit; they are not the safety identity.
- Partial indexes are supported by both dialects (SQLite ≥ 3.8.0, Postgres), so there is no dialect
  divergence and no new infrastructure. Declared once in `models/tables.py` with both `sqlite_where=`
  and `postgresql_where=`.

**What still protects Demo**, with no recipient-level rule: request idempotency (A, non-partial, binds
in both modes) · Origin-header checking · the public-write sliding-window limiter ·
`DEMO_MAX_ACTIONS_PER_RUN` · provider-mode binding (a demo run can never be handed `GmailSendProvider`) ·
zero network egress by construction. Nothing a demo visitor can do produces an outbound message or
touches live dedup state.

**Concurrency — unchanged, and still the database's job.** Two simultaneous *live* executes for two
different prospects resolving to the same address both pass the policy pre-check, both attempt the
insert; one wins the index, the loser catches `IntegrityError` and returns `409
ALREADY_SENT_TO_RECIPIENT`. The index is the guarantee; policy clause 12 (Part 6.1) performs the same
check beforehand purely to produce a good error message instead of a caught constraint violation. Belt
and braces, and the belt is the database — the same reasoning that put SSE sequencing in `UPDATE …
RETURNING` rather than application code. A live execution therefore cannot bypass the rule even by
racing.

**Follow-up sequences** would relax this only by introducing an explicit `action_purpose` /
`sequence_step` in the identity — folded into both the index and the idempotency key — so that "message
2 of a sequence" is a structurally different action rather than a second attempt at the same one.
`OutreachDraft.step_index` already exists as the carrier. Named as the extension point; **not built in
v2.**

### 3.6 Last-known-good enrichment

A later provider timeout must never destroy a previously derived, provider-backed channel state.

| Record | Written when | Job |
|---|---|---|
| `enrichment_calls` | every attempt, success or failure | telemetry — the full attempt history |
| `contact_enrichments` | every successful call (matched *or* explicit not-found — both are observations) | the immutable observation record |
| `contact_channels` | derived from the latest successful observation | the current, provider-backed state |

```
on a SUCCESSFUL enrichment call:
    insert contact_enrichments row
    upsert contact_channels:
        discovery_state / verification_state / identity_match_state / identifier  <- re-derived
        observed_at = observation time; derived_from_enrichment_id = the new row
        last_attempt_at/status/error_type = success

on a FAILED enrichment call:
    insert enrichment_calls row(s) only — no contact_enrichments row
    IF a contact_channels row already exists with a provider-backed state:
        UPDATE ONLY last_attempt_at, last_attempt_status, last_attempt_error_type
        -- the three state columns, the identifier, observed_at and
        -- derived_from_enrichment_id are NOT touched
    ELSE:
        upsert contact_channels with discovery_state = PROVIDER_ERROR, identifier = NULL
```

So `PROVIDER_ERROR` is a channel state only when no successful provider-backed observation has ever been
obtained. Once a channel has real state, a later failure is visible as attempt telemetry beside the
state, never as the state itself. The UI renders both: "VERIFIED (observed 12 Aug) · last refresh
attempt failed 3 Sep — provider timeout."

**Freshness, deliberately small.** `ENRICHMENT_STALE_AFTER_DAYS` (default 30) against
`contact_channels.observed_at`. A stale channel is badged in the UI and blocked by policy clause 5. No
background refresh, no TTL sweeper. Re-running the prospect is how you refresh, and that is an explicit
user action.

### 3.7 Deterministic LinkedIn identity matching *(Step 0 corrected in rev 3)*

Pure, in `domain/contact_identity.py`. No LLM, no fuzzy matching, no edit distance. Versioned as
`IDENTITY_MATCH_VERSION = "v1"` and `IDENTIFIER_GRAMMAR_VERSION = "v1"`, stored in
`contact_channels.derivation_version`.

#### Step 0 — origin-aware identifier grammar *(resolves C15)*

The observation's `origin` — a structural fact about which provider produced the row, never an
inference and never an LLM judgement — selects exactly one of two mutually exclusive grammars:

```python
def validate_linkedin_identifier(raw: str | None, *, origin: EnrichmentOrigin) -> IdentifierVerdict:
    if raw is None:
        return IdentifierVerdict.ABSENT

    if origin is EnrichmentOrigin.LIVE_PROVIDER:
        # UNCHANGED from rev 2 — deliberately strict, not weakened.
        #   1. scheme is exactly "https"
        #   2. passes domain/url_safety.py
        #   3. registrable domain (domain/psl.py) == "linkedin.com"
        #   4. path matches ^/in/[A-Za-z0-9\-_%]{1,120}/?$
        #   5. no userinfo, no port, no fragment
        # A demo:// value fails clause 1 -> REJECTED.
        ...

    if origin is EnrichmentOrigin.DEMO_FIXTURE:
        #   1. matches ^demo://linkedin/[a-z0-9][a-z0-9\-]{0,119}$ exactly
        # Any http:// or https:// value fails -> REJECTED.
        ...
```

`REJECTED` ⇒ `LinkedInResolutionState.NOT_FOUND`. The URL never becomes a `RESOLVED` identifier, so it
can never be surfaced or acted on. Both grammars fail closed and each rejects the other's shape.

Enforcement happens twice, mirroring the "secrets are scrubbed twice, not once" discipline:
1. a `contact_enrichments` model validator — a `DEMO_FIXTURE` row physically cannot persist an `http(s)`
   LinkedIn URL, and a `LIVE_PROVIDER` row cannot persist a `demo://` one;
2. this pure derivation — even if a row somehow existed, it would derive `NOT_FOUND`.

This is `Evidence._no_fake_sources` applied to a second identifier class, and it keeps the fixture
principle intact: the fixture still supplies only a provider observation (`profile_url`,
`asserted_full_name`, `asserted_company_name`, `asserted_company_domain`), and Groundwork computes
`RESOLVED` + `STRONG_MATCH` from it. Northwind resolves as the Demo matrix requires, and no fabricated
real-looking LinkedIn URL exists anywhere in the repository.

**Rendering rule (Part 7, Part 14).** A `demo://` identifier is never emitted as an external hyperlink —
the `EvidenceCard` origin-gate precedent, applied verbatim.

#### Step 1 — text normalization (`_norm_text`), applied identically to both sides

1. Unicode NFKC.
2. ASCII-fold: NFD-decompose, drop combining marks (category `Mn`), recompose — `José` → `Jose`.
3. `str.casefold()` (not `.lower()` — handles `ß` → `ss`).
4. Replace every Unicode punctuation/symbol character (categories `P*`, `S*`) with a single space.
5. Collapse whitespace runs; strip.

#### Step 2 — person-name matching

Tokenize; drop tokens in `{mr, mrs, ms, mx, dr, prof, rev, jr, sr, ii, iii, iv, v, phd, md, mba, cfa,
cpa, pmp, esq}`. A single-character token is an initial.

| Condition | Result |
|---|---|
| either side absent, or fewer than 2 surviving tokens on either side | `PERSON_UNKNOWN` |
| last tokens equal **and** (first tokens equal **or** one is an initial of the other) | `PERSON_MATCH` |
| otherwise | `PERSON_CONFLICT` |

Middle tokens ignored. Nicknames are not matched — `jon` vs `john` is a `PERSON_CONFLICT`, by design.

#### Step 3 — company matching, strict precedence

1. **Domain equality — preferred whenever available.** Both sides through `domain/psl.py`'s
   registrable-domain normalization. Equal → `COMPANY_MATCH`; unequal → `COMPANY_CONFLICT`.
2. **Name equality**, only if no domain on either side. `_norm_text`, then strip trailing
   corporate-suffix tokens from `{inc, llc, ltd, limited, corp, corporation, co, company, plc, gmbh, ag,
   sa, sas, srl, spa, bv, nv, ab, oy, as, aps, pty, pte, kk, holdings, group}`. Compare remaining token
   sequences. Never add identity-bearing words like `labs`, `ai`, `technologies`, `systems`.
3. Either side absent → `COMPANY_UNKNOWN`.

#### Step 4 — combination, fail-closed on contradiction

| person | company | `LinkedInIdentityState` |
|---|---|---|
| `PERSON_CONFLICT` | *any* | **MISMATCH** |
| *any* | `COMPANY_CONFLICT` | **MISMATCH** |
| `PERSON_MATCH` | `COMPANY_MATCH` | `STRONG_MATCH` |
| `PERSON_MATCH` | `COMPANY_UNKNOWN` | `WEAK_MATCH` |
| `PERSON_UNKNOWN` | `COMPANY_MATCH` | `WEAK_MATCH` |
| `PERSON_UNKNOWN` | `COMPANY_UNKNOWN` | `UNKNOWN` |

A contradiction on either axis is `MISMATCH` even when the other matches — a right name at the wrong
company is precisely what must not be actionable.

`ProviderLinkedInObservation.asserted_company_domain` is populated only if the provider supplies it;
Apollo's shape is unverified until V2-D, so the derivation must be correct with it absent, falling
through to name matching. That is why the precedence order exists.

### 3.8 Email identity normalization *(rev 3 — new)*

`domain/contact_identity.py::normalize_email_identity()` — pure, deterministic, versioned
`EMAIL_IDENTITY_VERSION = "v1"`. Used for two purposes, and the same function for both so they can never
disagree: the recipient-level send policy (§3.5B) and the hash's identifier canonicalization (§3.9).

```python
def normalize_email_identity(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw).strip()
    if s.count("@") != 1:
        raise InvalidEmailIdentity(...)          # fail closed — never a silent pass-through
    local, domain = s.rsplit("@", 1)
    if not local or not domain:
        raise InvalidEmailIdentity(...)
    domain = domain.rstrip(".").casefold()
    domain = _idna_encode(domain)                # punycode; unicode and ASCII forms collapse to one key
    local = local.casefold()                     # see note below
    return f"{local}@{domain}"
```

Two normalization decisions, both made in the fail-closed direction:

- **The local part IS casefolded.** RFC 5321 says the local part is technically case-sensitive and only
  the owning server may declare otherwise. For a *safety* rule, over-blocking is harmless and
  under-blocking is the actual harm — treating `Priya@x.com` and `priya@x.com` as two identities would
  permit a second initial send to what is, in every real deployment, one human. Deliberately
  over-inclusive.
- **Plus-tags and dots are NOT stripped.** Those are provider-specific folding rules (Gmail's, not the
  internet's). Applying them universally would silently merge genuinely distinct mailboxes at providers
  that treat them as significant. The line is: apply only normalizations that are universally true;
  refuse provider-specific folding.

Both choices are documented in the module docstring so the next reader sees the reasoning rather than
guessing at it, and both are unit-tested with the reasoning quoted.

### 3.9 Content/action hash — normative specification *(rev 3 — restored and extended)*

```python
HASH_VERSION = "v1"

def _canonical_text(value: str | None) -> str | None:
    """NFC, LF line endings, no trailing whitespace per line, no leading/trailing blank lines."""
    if value is None:
        return None
    s = unicodedata.normalize("NFC", value)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(line.rstrip(" \t") for line in s.split("\n"))
    return s.strip("\n")

def _canonical_identifier(channel: Channel, value: str | None) -> str | None:
    if value is None:
        return None
    if channel is Channel.EMAIL:
        return normalize_email_identity(value)      # §3.8 — the SAME function as the send policy
    return _canonical_text(value)                   # LINKEDIN: no casefold; slugs are compared exactly

def content_hash(*, channel, sender_identifier, recipient_identifier, subject, body) -> str:
    payload = {
        "hash_version":         HASH_VERSION,
        "channel":              channel.value,
        "sender_identifier":    _canonical_identifier(channel, sender_identifier),   # None for LINKEDIN
        "recipient_identifier": _canonical_identifier(channel, recipient_identifier),
        "subject":              _canonical_text(subject),                            # None for LINKEDIN
        "body":                 _canonical_text(body),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

**Included** — exactly the authorization-relevant, transmitted surface, and nothing else: `hash_version`,
`channel`, `sender_identifier`, `recipient_identifier`, `subject`, `body`.

**Excluded, explicitly:** draft id, draft version, proposal id, approval id, prospect id, run id,
`claim_map`, `evidence_ids`, every timestamp, `policy_version`, `policy_snapshot`, provider names — and
every OAuth credential (D13: the sender is an address, never a token, refresh token, client secret, or
scope string). Excluding non-transmitted metadata is what stops a cosmetic re-run gratuitously voiding a
valid approval.

**Invariants, each a unit test:**
- changing any of sender / recipient / subject / body changes the hash;
- changing any excluded field does not;
- the hash is stable across processes, platforms, and Python versions (no `hash()`, no dict ordering, no
  locale dependence);
- `hash_version` is stored on **both** the proposal and the approval — see Part 5 for the
  `approvals.hash_version` column and its CHECK constraint. **An ACTION-scope approval whose
  `hash_version` does not equal both the proposal's and the current `HASH_VERSION` is `SUPERSEDED`,
  never silently revalidated** (§6.1 clause 9). Nothing has shipped, so there is no v0 to migrate; the
  field exists so a *future* change is detectable rather than silent.
- `normalize_email_identity` is idempotent, so hashing an already-canonical stored identifier (§3.10) is
  safe: `normalize(normalize(x)) == normalize(x)` is a property test.

**Enforcement at execute time**, in order, before any provider call:
1. `approval.scope == "ACTION"` and `approval.action_proposal_id == proposal.id`;
2. `approval.hash_version == proposal.hash_version == HASH_VERSION` — else `409 APPROVAL_SUPERSEDED`;
3. re-resolve the currently connected Gmail account → `live_sender`;
4. `hmac.compare_digest(normalize_email_identity(live_sender), proposal.sender_identifier)` — mismatch
   blocks (§6.1 clauses 10–11);
5. recompute `content_hash` from the live draft + resolved recipient + `live_sender`;
6. `hmac.compare_digest` against `approval.content_hash` — mismatch → `409 CONTENT_CHANGED`;
7. fresh full `action_policy.evaluate()`.

### 3.10 Sender/recipient canonicalization contract *(rev 4 — new)*

Casing and Unicode representation differences must never produce inconsistent sender-match or dedup
behaviour. Each layer's form is pinned:

| Location | Form | Why |
|---|---|---|
| `gmail_connections.google_account_email` | provider display form, verbatim as Google returned it | It is what the account *is*; showing an operator a mangled version would be wrong. Never normalized in place. |
| `action_proposals.sender_identifier` | `normalize_email_identity(google_account_email)` | Computed once, at proposal creation. Canonical from birth. |
| `action_executions.sender_identifier` | copied from the proposal | Already canonical; never re-derived, so it cannot drift. |
| `action_proposals.recipient_identifier` | display form (the observed address) | This is what goes in the `To:` header — we transmit the address as observed, never a casefolded local part. |
| `action_proposals.recipient_identity_key` | `normalize_email_identity(recipient_identifier)` | Computed once; the execution copies it rather than re-deriving. |
| `action_executions.recipient_identity_key` | copied from the proposal | Feeds the §3.5B index. |
| `content_hash` sender/recipient inputs | `_canonical_identifier(...)` → the same `normalize_email_identity` | One function for identity, dedup **and** hashing, so the three can never disagree. |
| At execute | `hmac.compare_digest(normalize_email_identity(live_connected_email), proposal.sender_identifier)` | Both sides canonical; constant-time. |

**Deliberate consequence, stated rather than left implicit:** because the hash canonicalizes the
recipient, a casing-only edit to the display form does **not** void an approval. That is correct and
required for consistency — if two casings are one identity for the duplicate-send rule, they must be one
identity for the hash too. Any change beyond casing/Unicode-representation still voids it.

---

## Part 4 — Provider interfaces

New module `providers/contact_base.py` — separate from `providers/base.py` only to avoid a 700-line
file; same idioms, same error-hierarchy shape, same telemetry field names.

```python
class EnrichmentOrigin(StrEnum):                 # rev 3 — selects the identifier grammar (§3.7 Step 0)
    DEMO_FIXTURE  = "DEMO_FIXTURE"
    LIVE_PROVIDER = "LIVE_PROVIDER"

class EnrichmentOperation(StrEnum):
    PERSON_ENRICHMENT  = "person_enrichment"
    EMAIL_VERIFICATION = "email_verification"     # slot for a dedicated verifier; unused in v2

class EnrichmentAttemptStatus(StrEnum):
    OK; NOT_FOUND; TIMEOUT; RATE_LIMITED; AUTH_ERROR; PROVIDER_ERROR
    INVALID_RESPONSE; QUOTA_EXHAUSTED; NOT_ATTEMPTED_BUDGET

class EnrichmentAttemptTelemetry(BaseModel):     # mirrors SearchAttemptTelemetry field-for-field
    provider, operation, call_group_id, attempt, attempt_kind, status,
    started_at, finished_at, latency_ms, http_status, provider_request_id,
    error_type, error_message (redact() before set), cost_usd, credits_used,
    input_digest, output_digest

class PersonEnrichmentQuery(BaseModel):
    full_name: str | None; title: str | None; company_name: str; company_domain: str

class ProviderEmailObservation(BaseModel):
    """What the provider ASSERTED. Never a Groundwork verdict."""
    address: str | None = None
    provider_status: str | None = None           # the provider's own raw word, verbatim
    provider_confidence: float | None = None
    is_catch_all: bool | None = None
    observed_at: datetime

class ProviderLinkedInObservation(BaseModel):
    profile_url: str | None = None
    asserted_full_name: str | None = None
    asserted_company_name: str | None = None
    asserted_company_domain: str | None = None   # only if the provider supplies it
    asserted_title: str | None = None
    observed_at: datetime

class PersonEnrichmentResult(BaseModel):
    matched: bool
    provider_person_id: str | None = None
    email: ProviderEmailObservation | None = None
    linkedin: ProviderLinkedInObservation | None = None
    origin: EnrichmentOrigin                     # rev 3 — carried, never inferred downstream
    raw_digest: str                              # digest only — raw payloads never persisted
    telemetry: list[EnrichmentAttemptTelemetry] = []

class EnrichmentProvider(Protocol):
    name: str
    origin: EnrichmentOrigin                     # a static property of the implementation
    async def enrich_person(self, q: PersonEnrichmentQuery, *, ctx_key: str) -> PersonEnrichmentResult: ...
```

Errors mirror the LLM/search hierarchies: `EnrichmentProviderError` (carries `.telemetry`), with
`EnrichmentTimeout` / `EnrichmentRateLimited` / `EnrichmentProviderUnavailable` step-retryable, and
`EnrichmentAuthError` / `EnrichmentInvalidResponse` / `EnrichmentQuotaExceeded` permanent.
`STEP_RETRYABLE` in `providers/base.py` is documented as the complete, exhaustive step-level-retryable
set, so enrichment gets its own retryable tuple rather than appending to it.

Apollo→state mapping lives with the adapter, not in `domain/`:

```python
# providers/live/apollo_enrichment.py
APOLLO_EMAIL_STATUS_MAP: Mapping[str, EmailVerificationState] = { ... }   # keys confirmed in V2-D
                                                                          # unmapped ⇒ UNVERIFIED (fail closed)
```

passed into the pure derivation:

```python
# domain/contact_identity.py  (pure — no provider imports)
def derive_email_channel(obs, *, status_map) -> tuple[EmailDiscoveryState, EmailVerificationState]
def derive_linkedin_channel(obs, *, origin, grounded_full_name,
                            grounded_company_name, grounded_company_domain)
        -> tuple[LinkedInResolutionState, LinkedInIdentityState]
```

**Outbound execution:**

```python
class ActionType(StrEnum):
    EMAIL_SEND             = "EMAIL_SEND"
    LINKEDIN_COPY_AND_OPEN = "LINKEDIN_COPY_AND_OPEN"
    # There is deliberately no LINKEDIN_SEND. Nothing can invoke what does not exist.

class ActionExecutionOrigin(StrEnum):             # rev 4 — decides which safety rules bind (D14)
    DEMO_SIMULATED = "DEMO_SIMULATED"   # zero network egress; demo:// message id;
                                        #   @groundwork.invalid sender; NOT bound by §3.5B
    LIVE_EXTERNAL  = "LIVE_EXTERNAL"    # execution on the live external-action path — capable of a real
                                        #   external side effect; bound by §3.5B. NOT itself proof that a
                                        #   message left the system or was delivered — that is
                                        #   execution status/outcome (SUCCEEDED/FAILED/UNCERTAIN/etc.).

class SendOutcome(StrEnum):
    ACCEPTED; PROVEN_NOT_DISPATCHED; DEFINITIVE_REJECTION
    ACCEPTANCE_UNKNOWN                            # the DEFAULT for anything not positively classified

class OutboundEmailMessage(BaseModel):
    to: str; subject: str; body_text: str
    message_id_header: str          # WE generate it; the provider must preserve it verbatim

class SendResult(BaseModel):
    outcome: SendOutcome
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    dispatched: bool                 # was the body written to the transport? sets dispatched_at
    telemetry: list[SendAttemptTelemetry] = []

class ReconcileStatus(StrEnum):
    FOUND
    NOT_FOUND_WITHIN_BOUNDS          # NOT evidence of non-delivery
    UNSUPPORTED                      # provider cannot reconcile at all
    LOOKUP_FAILED                    # the reconciliation call itself failed

class ReconcileResult(BaseModel):
    status: ReconcileStatus
    provider_message_id: str | None = None
    messages_scanned: int = 0
    scanned_past_dispatch: bool = False
    telemetry: list[SendAttemptTelemetry] = []

class EmailSendProvider(Protocol):
    name: str
    supports_message_id_lookup: bool

    async def connected_account_identifier(self) -> str | None:
        """rev 3 — the identity that WILL send, resolved fresh. An email address, never a
        credential (D13). Returns None when nothing is connected."""

    async def send(self, msg: OutboundEmailMessage, *, idempotency_key: str) -> SendResult: ...
    async def find_sent_message(
        self, *, message_id_header: str, sent_after: datetime, bounds: ReconcileBounds
    ) -> ReconcileResult: ...
```

`NOT_FOUND_WITHIN_BOUNDS` rather than `None` is the point: the type system refuses to let a caller read
"we didn't find it" as "it wasn't sent." `connected_account_identifier()` is called at two moments —
proposal creation (to capture `sender_identifier`) and execute (to re-verify it) — which is what makes
C16's disconnect-and-reconnect attack detectable.

`DemoEmailSendProvider.connected_account_identifier()` returns the synthetic
`demo-sender@groundwork.invalid`. `.invalid` is an IANA-reserved TLD that can never resolve, so a demo
sender identity is structurally incapable of being a real person's address.

---

## Part 5 — Additive persistence (design only; no migration written at V2-A)

No v1 column is dropped, retyped, or made stricter. New tables in `models/tables.py` (SQLite via
`create_all`) plus one additive Alembic revision for Postgres, verified by `test_migration_drift.py`.
SQLite stays `create_all()`-managed and must never gain an Alembic path.

**Modified additively:**
- `outreach_drafts`: `+ content_hash (String, null)`, `+ hash_version (String, default "v1")`;
  `subject` → nullable (`DROP NOT NULL`, no rewrite); `+ index (prospect_id, channel)`.
- `approvals`: `+ action_proposal_id (FK, null)`, `+ content_hash (String, null)`,
  **`+ hash_version (String, null)`** *(rev 4)*, `+ scope (String, default "PROSPECT", server_default)`.
  Every existing v1 row remains valid — it takes `scope="PROSPECT"` from the server default and leaves
  the three new columns `NULL`. `/approve` and `/reject` are unchanged. No `override_reasons` column —
  D7 removed the need.

  An `ACTION`-scope approval must carry all three, enforced structurally rather than by convention:

  ```sql
  ALTER TABLE approvals ADD CONSTRAINT ck_approvals_action_scope_complete CHECK (
      scope <> 'ACTION'
      OR (action_proposal_id IS NOT NULL
          AND content_hash    IS NOT NULL
          AND hash_version    IS NOT NULL)
  );
  ```

  Additive-safe: every pre-existing row has `scope='PROSPECT'` and passes. Postgres validates the
  constraint with a brief scan of a table holding a handful of rows — a `NOT VALID` + `VALIDATE` split
  is available but unnecessary at this size. SQLite gets the same `CheckConstraint` through
  `create_all`.
- `contacts`: **unchanged.**

**New tables:**

| Table | Grain | Key columns |
|---|---|---|
| `contact_enrichments` | one row per successful observation group | prospect_id, provider, call_group_id, matched, provider_person_id, email_address, email_provider_status, email_provider_confidence, email_is_catch_all, linkedin_url, linkedin_asserted_{name,company,company_domain,title}, observed_at, origin, raw_digest · `UNIQUE(prospect_id, provider, call_group_id)` |
| `enrichment_calls` | one row per provider call attempt, success or failure | call_group_id, attempt, attempt_kind, operation, run_id, prospect_id, provider, status, timings, http_status, provider_request_id, error_type, error_message (redacted), cost_usd, credits_used, digests · `UNIQUE(call_group_id, attempt)` |
| `contact_channels` | one row per (prospect, channel) — the latest successfully derived state | prospect_id, channel, identifier, discovery_state, verification_state, identity_match_state, derivation_version, derived_from_enrichment_id, observed_at, last_attempt_at, last_attempt_status, last_attempt_error_type · `UNIQUE(prospect_id, channel)` |
| `action_proposals` | immutable | prospect_id, run_id, draft_id, action_type, channel, `sender_identifier` (canonical — §3.10; NULL for LINKEDIN), `recipient_identifier` (display form), `recipient_identity_key` (canonical — §3.10, rev 4), content_hash, hash_version, policy_version, policy_verdict, blocked_reasons (JSON), policy_snapshot (JSON), `origin: ActionExecutionOrigin` (bound by run mode), created_at, superseded_by · `UNIQUE(draft_id, content_hash)` |
| `action_executions` | the execution record | action_proposal_id, approval_id, prospect_id, run_id, action_type, provider, status, `idempotency_key UNIQUE` (non-partial — binds in BOTH origins), `recipient_identity_key` (copied from the proposal; populated for EMAIL_SEND in both origins; NULL for LINKEDIN), `sender_identifier` (copied; canonical), `origin: ActionExecutionOrigin` (rev 4), message_id_header, provider_message_id, provider_thread_id, executor_id, dispatched, outcome_class, attempt_count, reconcile_attempts, messages_scanned, claimed_at, dispatched_at, settled_at, reconciled_at, last_error_{type,message} · index `(status, claimed_at)` · partial unique index `uq_action_executions_live_recipient`, predicated on `origin='LIVE_EXTERNAL'` (§3.5B) |
| `action_send_calls` | one row per send or reconcile attempt | telemetry shape as `enrichment_calls`, plus `operation ∈ {send, reconcile}`, keyed to `action_execution_id` |
| `action_events` | append-only audit trail, never updated | action_execution_id?, action_proposal_id?, prospect_id, type, actor, payload (JSON, redacted), ts · index `(prospect_id, ts)` |
| `gmail_connections` | exactly one row (`id="default"`) | google_account_email, encrypted_refresh_token, key_version, scopes (JSON), connected_at, connected_by_actor, last_refreshed_at, revoked_at. Access tokens never persisted. |
| `oauth_states` | short-lived, single-use | state (PK), pkce_verifier, created_at, expires_at, consumed_at |

Untouched: `runs`, `plays`, `prospects`, `companies`, `evidence`, `signals`, `icp_scores`,
`review_results`, `agent_tasks`, `llm_calls`, `search_calls`, `source_documents`, `run_events`.

**Four model validators** — the `Evidence._no_fake_sources` precedent, extended (D9):
1. a `DEMO_FIXTURE`-origin `contact_enrichments` row may not carry an `http(s)` LinkedIn URL, and its
   `linkedin_url` must match the `demo://linkedin/…` grammar;
2. a `LIVE_PROVIDER`-origin `contact_enrichments` row may not carry a `demo://` identifier;
3. a `DEMO_SIMULATED`-origin `action_executions` row's `provider_message_id` must start with `demo://`;
4. a `DEMO_SIMULATED`-origin `action_proposals` row's `sender_identifier` must end in
   `@groundwork.invalid`.

**Insert ordering is load-bearing.** No ORM `relationship()` exists anywhere and `PRAGMA
foreign_keys=ON` is set in both `db.py` and `conftest.py`, so any repository method writing a row
referencing an id created earlier in the same transaction needs the `add → flush() → add → commit`
pattern from `create_play_with_attempts`. Applies to `contact_enrichments → contact_channels` and
`action_proposals → approvals → action_executions`.

**Partial-index implementation note (V2-B risk).** Declared once as `Index(..., unique=True,
sqlite_where=…, postgresql_where=…)`. Alembic's `compare_metadata` can produce a false drift signal when
Postgres reflects a partial predicate in a canonical form differing textually from the declaration.
Mitigation at V2-B: verify drift-clean against a real Postgres; if a false positive appears, match
Postgres's canonical rendering rather than reaching for an `include_object` exclusion — excluding the
index from drift detection would silently un-protect the constraint this whole mechanism depends on.

---

## Part 6 — Review policy changes

**Still exactly seven checks.** Two are modified; none added; no LLM enters the path.

**`no_fabricated_contact` — rewritten (HARD).** Today: "if an email or LinkedIn URL is present, the
contact must be VERIFIED" — which C2 shows becomes actively wrong once identifiers are real. New,
per-axis and provenance-based:
1. every identifier on `contact_channels` must resolve to a real `contact_enrichments` row — an
   identifier with no provider observation behind it is a hard fail;
2. a LinkedIn identifier present with `identity_match_state == MISMATCH` is a hard fail;
3. no identifier-shaped token appears in any draft's subject/body that is not one of this prospect's own
   provider-observed identifiers — an email regex and a `linkedin.com/in/` regex over the rendered text.
   The deterministic backstop for D3, folded into the check whose name already covers it.

It no longer reads `contact.verification` at all — correct, because that enum is the person-identity
axis and says nothing about reachability.

**`no_placeholders` — channel-aware (HARD).** The empty-subject clause applies only to channels carrying
a subject; body-empty stays universal. The bracket/angle-bracket patterns from commit `b213bc6` are
untouched, including the regression test quoting the real production `[Your Name]` case.

**Unchanged:** `claim_grounding` (already loops over all drafts), `cross_prospect_leak`,
`duplicate_account`, `score_support`, `confidence_floor`.

### 6.1 Deterministic action policy (`domain/action_policy.py` — pure)

Returns `(verdict, blocked_reasons: list[str], policy_snapshot: dict)`. `policy_version` stored on every
proposal, like `rubric_version` on scores. No clause has an override (D7).

**EMAIL_SEND:**

| # | Clause | Blocked reason |
|---|---|---|
| 1 | review verdict is `PASS` — hard floor | `review_not_passed` |
| 2 | prospect status ∉ {REJECTED, FAILED, DUPLICATE, TIMED_OUT, PENDING, RUNNING} | `prospect_not_actionable` |
| 3 | `contact_channels[EMAIL].discovery_state == FOUND` | `email_not_discovered` |
| 4 | `contact_channels[EMAIL].verification_state == VERIFIED` | `email_not_verified` |
| 5 | `observed_at` within `ENRICHMENT_STALE_AFTER_DAYS` (§3.6) | `contact_state_stale` |
| 6 | draft channel is EMAIL; subject and body both non-empty | `draft_incomplete` |
| 7 | recipient normalizes successfully under §3.8 | `recipient_identity_invalid` |
| 8 | recomputed `content_hash` equals the proposal's (§3.9) | `content_changed` |
| 9 | `approval.hash_version == proposal.hash_version == HASH_VERSION` *(rev 4)* | `approval_superseded` |
| 10 | a send identity is currently connected | `sender_not_connected` |
| 11 | the connected identity matches `proposal.sender_identifier` (both canonical, §3.10) | `sender_changed` |
| 12 | **LIVE ONLY** *(rev 4)* — when `proposal.origin == LIVE_EXTERNAL`: no execution anywhere with `origin == LIVE_EXTERNAL` and this `recipient_identity_key` in {SUCCEEDED, UNCERTAIN, ABANDONED, CLAIMED, IN_FLIGHT} — cross-run, cross-prospect (§3.5B). Skipped entirely for a `DEMO_SIMULATED` proposal, and it never inspects `DEMO_SIMULATED` rows. | `already_sent_to_recipient` / `prior_send_uncertain` / `send_in_flight` |
| 13 | a send provider is configured for this run's mode | `send_provider_unavailable` |
| 14 | daily send allowance not exhausted (live) / `DEMO_MAX_ACTIONS_PER_RUN` not exhausted (demo) | `send_allowance_exhausted` / `demo_action_cap_reached` |

Clause 12 is the only clause whose applicability depends on origin, and it is symmetrically isolated: a
demo proposal is never blocked by anything, and never blocks anything. Every other clause — including
the `PASS` floor, the `VERIFIED` floor, staleness, the hash, and `approval_superseded` — binds
identically in both modes, so a Demo walkthrough exercises the *real* policy rather than a relaxed one.

**LINKEDIN_COPY_AND_OPEN:** clauses 1, 2, 5, 6 (body only), 8, 9, plus
`contact_channels[LINKEDIN].discovery_state == RESOLVED` (`linkedin_not_resolved`) and
`identity_match_state == STRONG_MATCH` (`linkedin_identity_not_strong`). Clauses 3–4, 7, 10–14 do not
apply: copying a message is not a send, there is no sender identity and no executor (D6).

Reasons are free-form strings aggregated generically by `/evaluation`, following the
`discovery.candidate_rejected` precedent — a new reason needs no metrics code change.

---

## Part 7 — Demo Mode

Fixture format extension — observations, never verdicts:

```yaml
enrichment:
  matched: true
  email:
    address: priya.natarajan@northwindlabs.com
    provider_status: verified          # the PROVIDER's word, not ours
    provider_confidence: 0.94
    is_catch_all: false
  linkedin:
    profile_url: demo://linkedin/priya-natarajan   # the ONLY grammar a DEMO_FIXTURE row may carry
    asserted_full_name: Priya Natarajan
    asserted_company_name: Northwind Labs
    asserted_company_domain: northwindlabs.com     # matches CompanySeed.domain -> COMPANY_MATCH
    asserted_title: VP of Sales
enrichment_failure_script:
  person_enrichment: { fail_attempts: 0, error: EnrichmentProviderUnavailable }
```

Groundwork — not the fixture — computes `RESOLVED` + `STRONG_MATCH` from those observations, via §3.7's
`DEMO_FIXTURE` grammar and domain-equality path. The fixture principle is intact and no fabricated
real-looking LinkedIn URL exists anywhere in the repository.

**Demo sender identity:** `demo-sender@groundwork.invalid`, validator-enforced (Part 5, validator 4).
`.invalid` is IANA-reserved and can never resolve, so a demo sender is structurally incapable of being a
real person's address.

**Demo rendering rules — the `EvidenceCard` origin gate applied verbatim:**
- A `demo://` LinkedIn identifier is never rendered as an `<a href>`. It renders as a "Synthetic · demo
  fixture" chip beside the slug, exactly as `DEMO_FIXTURE` evidence renders today.
- The `LINKEDIN_COPY_AND_OPEN` action stays a real action in Demo. Copy performs a genuine clipboard
  write of the actual draft. Open reveals an inline simulated profile panel built from the fixture's own
  asserted observations (name, title, company, domain) — no navigation, no fake external URL.
- In Live, Open navigates to the validated real `https://linkedin.com/in/…` URL in a new tab
  (`target="_blank" rel="noreferrer noopener"`), as today's `ContactPanel` already does for links.
- The recorded `action_executions` row is identical in shape in both modes; only the presentation of the
  identifier differs, selected by origin — which is precisely the existing precedent, not a new special
  case.

Demo matrix, chosen so `target_count` stays 7 and the canonical board is unchanged:

| Company | v1 outcome (unchanged) | v2 enrichment outcome |
|---|---|---|
| Northwind Labs | PASS 92, VERIFIED (Priya Natarajan) | email VERIFIED · LinkedIn RESOLVED + STRONG_MATCH (name + domain) → both actions eligible. The hero path. |
| Sable Compute | PASS 79, VERIFIED (Marcus Webb) | email RISKY (catch-all) · LinkedIn STRONG_MATCH → email permanently blocked, LinkedIn allowed. Proves the axes are independent; with D7 there is no button to override it. |
| Riverbend Analytics | NEEDS_REVIEW 35, PERSONA_ONLY | `NOT_ATTEMPTED` on both — no named person to look up. Not an error. |
| Cobalt Retail Systems | REJECTED 25 (excluded industry) | not attempted; never actionable |
| Ferrous Grid | NEEDS_REVIEW 58, UNAVAILABLE | nothing to enrich |
| Quarry Systems | FAILED (retries exhausted) | never reaches enrichment |
| Northwind Labs Inc. | DUPLICATE | pipeline skipped |

`PROVIDER_ERROR`, last-known-good preservation, `MISMATCH`, cross-grammar rejection, the cross-run
recipient block, and the ambiguous-send path are all covered by a dedicated test fixture pack, not the
canonical demo pack — so the canonical board's counts, statuses, scores and verdicts stay
byte-identical. Appending to a fixture snippet is safe while removing text is not; the `enrichment:`
block is purely additive and touches no existing source.

Demo Mode *can* execute an `EMAIL_SEND`: `DemoEmailSendProvider` writes a real `action_executions` row
with `origin=DEMO_SIMULATED`, a `demo://` message id, and the synthetic sender — touching no network.
That is what makes the whole action architecture demoable with no credentials, and it is
**zero-egress by construction**: no socket is opened, no DNS lookup performed, no external service
contacted.

---

## Part 8 — Live Mode failure behaviour

No fixture fallback, ever.

| Situation | Behaviour |
|---|---|
| Enrichment requested, `APOLLO_API_KEY` absent | `ProviderNotConfigured` → `422` at run start. Never a Demo enrichment provider in a Live run. |
| Enrichment deliberately disabled (`ENRICHMENT_ENABLED=false`) | `NOT_ATTEMPTED`, zero provider calls, zero fixture data. Disabled ≠ fallback. |
| Enrichment configured, call fails | `enrichment_calls` records it; existing channel state preserved (§3.6); `PROVIDER_ERROR` only if no prior success. The prospect degrades (`contact_enrichment` is `optional=True`); the run continues. |
| Live send, Gmail not connected | `409 GMAIL_NOT_CONNECTED` (clause 9). No demo-send fallback in live mode; no live-send in demo mode. The sender is bound by run mode and asserted by test in both directions. |
| Live send, Gmail reconnected to a different account after approval | `409 SENDER_CHANGED` (clause 10). A new proposal and a new approval are required. |
| Live send, acceptance unprovable | `UNCERTAIN` + bounded reconciliation (§3.3). Never a silent retry, never a resend. |
| Reconciliation unsupported or exhausted | Stays `UNCERTAIN`; the recipient identity stays blocked (clause 11). Honest degradation, not a scope escalation. |

---

## Part 9 — Security model

**Provider secrets.** `APOLLO_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`TOKEN_ENCRYPTION_KEY` — env only, never logged, never returned. `GET /settings/providers` reports
`configured: bool` only.

**The sending identity is not a secret, and secrets are not the sending identity (D13).**
`sender_identifier` is an email address: safe to persist on a proposal, safe to hash, safe to show an
operator, safe in an audit payload. The refresh token, access token, client secret and granted scopes
are none of those things and appear in exactly one place — the encrypted `gmail_connections` row.

**Gmail OAuth.** A server-side confidential client. Scopes, least privilege:
- `gmail.send` — the send itself.
- `gmail.metadata` — headers only, no bodies; the bounded `SENT` scan in §3.3.
- `gmail.readonly` is NOT requested and not assumed necessary. If V2-G shows metadata-only
  reconciliation is impossible, that becomes an explicit decision put to the user at the V2-I gate.

Controls:
- **`state`**: 32 random URL-safe bytes in `oauth_states`, short TTL, single-use, bound to the operator
  session. The load-bearing CSRF control for a confidential client.
- **PKCE (S256): defence-in-depth, documented as such** — it covers authorization-code interception, not
  the CSRF that `state` covers.
- `redirect_uri`: exact-match from config, never derived from the request.
- Refresh token: Fernet-encrypted under `TOKEN_ENCRYPTION_KEY`, with `TOKEN_ENCRYPTION_KEY_OLD`
  accepted for decryption only — mirroring `SESSION_SIGNING_KEY_OLD`, documented in `RUNBOOK.md`.
- Access tokens: never persisted, minted in-process from the refresh token.
- Disconnect: revokes at Google *and* deletes the row. Any proposal whose `sender_identifier` no longer
  matches a connected account is thereby unexecutable — disconnect is a safe operation by construction,
  not by convention.

**Capability gating** (`require_action_capability()`, routers only), mode-aware in the
`enforce_live_gate` shape so I1's "Demo Mode must never gain an operator-session dependency" stays true:

| Capability | Demo | Live |
|---|---|---|
| create an `ACTION`-scope approval | public, Origin-checked, rate-limited | operator session + Origin |
| execute an action | public, Origin-checked, rate-limited, capped per run | operator session + Origin |
| mark an execution `ABANDONED` | public (demo rows only) | operator session + Origin |
| connect/disconnect Gmail | operator only — no demo equivalent exists | operator only |

One deliberate deviation from `enforce_live_gate`'s shape: demo action endpoints are Origin-checked even
though demo is otherwise CSRF-unprotected. A forged cross-origin demo action is harmless but writes
audit rows, and the check is free.

**External Live action authorization — five server-side gates, all required:**
1. a valid operator session;
2. an `Approval` row for this proposal;
3. the currently connected sender identity matches `proposal.sender_identifier`;
4. a fresh `content_hash` recomputation matching `approval.content_hash` (`hmac.compare_digest`);
5. a fresh full `action_policy.evaluate()`.

The UI is never a gate. A forged request that skips the UI hits all five.

**Structural separation of demo and live execution.** `build_provider_bundle` binds the sender by run
mode. A demo run can never be handed `GmailSendProvider`; a live run can never be handed
`DemoEmailSendProvider`. Asserted by test in both directions — this is what makes D8's public demo
actions safe.

**Rate/abuse.** `LIVE_MAX_SENDS_PER_DAY` (DB-backed, correct across processes), global send concurrency
of 1, `DEMO_MAX_ACTIONS_PER_RUN` (default 10), the existing public-write sliding-window limiter on demo
action endpoints, and the recipient-level rule in clause 11.

**Prompt injection.** The injectable surface (fetched page text) and the identifier surface are
provably disjoint — identifiers come only from a provider row under an origin-bound grammar, and a
deterministic check rejects identifier-shaped text in generated drafts.

**Redaction.** Add an email-address rule to `observability/redact.py` (the existing choke point).
Recipient and sender addresses are never logged in full at INFO.

---

## Part 10 — Observability & quality metrics

Extend `/evaluation` — computed on read, no metrics table:

- **Enrichment:** attempted / matched / `match_rate`; `email_found_rate`, `email_verified_rate`,
  `catch_all_rate`; `linkedin_resolved_rate`, `identity_match_distribution`;
  `identifier_grammar_rejections` (by origin — a nonzero LIVE count means a provider returned a
  malformed profile URL, worth seeing); `provider_error_rate`; `stale_channel_count`;
  `preserved_last_known_good_count`; p50/p95 latency; `credits_used`; `cost_usd` (null unless priced).
- **Actions:** `proposals_by_verdict`; `blocked_reasons` as a generic `reason: count` map — which
  surfaces `already_sent_to_recipient` and `sender_changed` with no metrics code change;
  `approval_to_execution_latency_p50/p95`; `executions_by_status`; `uncertain_count`;
  `reconciliation_outcomes`; `mean_messages_scanned_per_reconcile`; `content_hash_mismatch_count`;
  `cross_run_recipient_blocks` (initial sends prevented because the address was already contacted in an
  earlier run — the number that proves §3.5B works).
- **Logging:** one structured line per enrichment call, send attempt, and reconcile attempt, carrying
  `run_id`/`prospect_id`/`action_execution_id`/`latency_ms`; never a full address, never a message body,
  never a scanned message's headers.

---

## Part 11 — Recommended simplifications

| Proposed | Recommendation |
|---|---|
| A separate email-verification provider (Hunter / NeverBounce) | Cut for v2. Apollo returns a status/confidence; the Protocol keeps an `EMAIL_VERIFICATION` slot. |
| A generic channel-plugin registry | Cut. Two channels, an enum, a per-channel policy branch. |
| A job queue / worker for sends | Cut. Sends are single, synchronous, human-triggered, idempotent. Reconciliation retries ride the existing stale-claim sweep. |
| A second "final confirmation" `Approval` row | Cut as a second row. The execute request carrying `approval_id` + the operator-visible `content_hash`, server-re-verified, *is* the confirmation. |
| PKCE as the primary OAuth control | Reframe, keep. `state` is primary; PKCE is defence-in-depth. |
| KMS / external secret manager | Cut. Fernet + an env key, same posture as `SESSION_SIGNING_KEY`. |
| Reusing `run_events` for the action audit | Cut. Actions outlive runs; reusing the per-run `seq` breaks SSE resumability. |
| A users table for Gmail identity | Cut. One operator-owned connection row. |
| Override paths for blocked actions | Cut entirely (D7). |
| Resend / follow-up sequences | Cut entirely (D10). Extension point named, not built. |
| Broadening OAuth scope for easy reconciliation | Cut (D11). Escalate only with evidence, at a gate. |
| A new dedup service / cache / cross-run registry *(rev 3)* | Cut. The recipient-level rule is one indexed column and one partial unique index on a table that already exists. No new infrastructure, and the DB — not application logic — is the guarantee. |

**Kept despite cost**, because they are the load-bearing ideas a founder will probe: content-hash-bound
approvals including the sender, the acceptance-unknown taxonomy, bounded evidence-based reconciliation,
and the recipient-centric duplicate-send identity.

---

## Part 12 — Branch, PR and session workflow

**Checkpoints are never pushed directly onto `feature/v2-contact-enrichment`.**

```
master                                  production; Render deploys this branch ONLY
  ↑  one PR, only after all v2 validation (V2-J)
feature/v2-contact-enrichment           integration branch; never deployed
  ↑  one PR per checkpoint
claude/v2-a-docs · claude/v2-b-domain-persistence · claude/v2-c-enrichment
claude/v2-d-live-apollo · claude/v2-e-enrichment-ui · claude/v2-f-channel-outreach
claude/v2-g-gmail-oauth · claude/v2-h-action-approval · claude/v2-i-gmail-execution
claude/v2-j-quality-release
```

Per checkpoint, without exception:
1. `git fetch origin feature/v2-contact-enrichment`; branch from its current head.
2. Implement and test; full suite on SQLite (and Postgres where a DSN is available).
3. `git push -u origin claude/v2-x-…`.
4. Open a PR **targeting `feature/v2-contact-enrichment`** — never `master`.
5. CI green + human review.
6. Merge only when approved.
7. A fresh session for the next checkpoint, bootstrapped from `CLAUDE.md` + `PROGRESS.md` alone.

`master` is untouched until the single final integration PR at V2-J. Render continues deploying
`master` only. The Neon `v2-development` child branch is the only database v2 work touches; the
`production` branch is never migrated during v2 development. Branch names follow the repo's existing
`claude/<slug>` convention.

---

## Part 13 — Checkpoint plan (V2-A through V2-J)

Two invariants at every phase gate: (a) canonical demo output byte-identical in statuses, scores, review
verdicts and evidence counts — baseline (438 tests on SQLite / 448 with a Postgres DSN) recorded verbatim
before any change; (b) zero paid provider calls in CI.

### V2-A — Architecture & docs *(no application code)* → `claude/v2-a-docs`
This document, plus the v2 section in `ARCHITECTURE.md`, the V2-0/V2-A entries in `PROGRESS.md`, and the
extended invariants in `CLAUDE.md`. *Accept:* docs merged, zero code diff. *Tests:* none.

### V2-B — Domain model + additive persistence → `claude/v2-b-domain-persistence`
Enums (incl. `EnrichmentOrigin` and `ActionExecutionOrigin`), schemas + four validators, tables (nine +
additive columns + `approvals.hash_version` + its CHECK + `recipient_identity_key` + the origin-scoped
partial unique index), one Alembic revision, and the pure domain modules.
*Accept:* `alembic upgrade head` clean on scratch Postgres; drift test green with both the partial index
and the CHECK constraint present; a pre-existing v1 `approvals` row survives the migration unchanged and
still reads back as a valid `PROSPECT`-scope approval (`hash_version` NULL); an `ACTION`-scope row
missing any of `action_proposal_id` / `content_hash` / `hash_version` is rejected by the constraint;
canonical demo unchanged.
*Tests:*
- `test_contact_identity.py` — every observation→state mapping; `PROVIDER_ERROR` ≠ `NOT_FOUND`; unmapped
  status fails closed; the full §3.7 matrix.
- `test_linkedin_identifier_grammar.py` *(the four required proofs)*: (a) a `DEMO_FIXTURE`
  `demo://linkedin/priya-natarajan` derives `RESOLVED` and, with matching name+domain, `STRONG_MATCH`;
  (b) a `DEMO_FIXTURE` row carrying `https://linkedin.com/in/x` is rejected at the validator **and**
  derives `NOT_FOUND`; (c) a `LIVE_PROVIDER` row carrying `demo://…` is rejected at the validator **and**
  derives `NOT_FOUND`; (d) malformed/non-LinkedIn LIVE URLs all fail closed to `NOT_FOUND`.
- `test_email_identity_normalization.py` — casefolding both parts; IDNA/unicode-vs-ASCII domain forms
  collapsing to one key; plus-tags and dots explicitly not stripped; invalid inputs raise rather than
  pass through; idempotence.
- `test_content_hash.py` — the §3.9 normative algorithm: sender, recipient, subject and body each
  independently change the hash; every excluded field does not; cross-process stability; `hash_version`
  mismatch ⇒ SUPERSEDED.
- `test_action_policy.py` — every clause in isolation; no override path exists; clause 12 blocks on
  `UNCERTAIN` and `ABANDONED` and permits only `FAILED`; clauses 10–11 block a changed sender; clause 9
  supersedes on a `hash_version` mismatch; clause 12 is skipped entirely for a `DEMO_SIMULATED` proposal
  and never inspects `DEMO_SIMULATED` rows.
- `test_migration_drift.py` stays green.

### V2-C — Enrichment boundary + Demo fixtures + pipeline step → `claude/v2-c-enrichment`
`providers/contact_base.py`, `DemoEnrichmentProvider` (`origin = DEMO_FIXTURE`),
`engine/enrichment.py::call_enrichment`, `EnrichmentCallBudget`, the `contact_enrichment` step
(`optional=True`, after `contact`, before `personalize` — never named `enrich`), `ProspectContext`
fields, the §3.6 guarded-update repository method, aggregate exposure, fixture-pack extension.
*Accept:* a Demo run produces `contact_channels` matching the Part 7 matrix — Northwind
`RESOLVED`+`STRONG_MATCH` from a `demo://` identifier; canonical board unchanged.
*Tests:* `test_provider_purity.py` extended; `test_demo_enrichment_provider.py`;
`test_enrichment_last_known_good.py`; `test_fixture_provenance.py` extended; `test_isolation.py`
extended.

### V2-D — Live Apollo enrichment *(the only money-spending checkpoint)* → `claude/v2-d-live-apollo`
`providers/live/apollo_enrichment.py` (`origin = LIVE_PROVIDER`) + a process-scoped `ApolloRuntime`, the
status map, bounds/budget wiring, `scripts/enrichment_smoke.py` guarded by
`--i-understand-this-costs-money` + a configured key, never automated, never in CI.
*Accept:* one manual smoke against ≤2 real people; the confirmed response shape, status vocabulary, and
whether Apollo supplies an organization domain recorded in `PROGRESS.md`. Live without a key 422s.
*Tests:* adapter against `httpx.MockTransport` only.

### V2-E — Contact enrichment UI → `claude/v2-e-enrichment-ui`
`ContactPanel` gains the five axes — each with its own badge and "why" sentence — a provenance chip per
identifier, the `last_attempt_*` line beside preserved state, and a staleness badge. Origin-gated
identifier rendering: `demo://` renders as a synthetic chip and an inline simulated-profile panel; a
validated `https://linkedin.com/in/…` renders as a real external anchor. `lib/types.ts` additions
(hand-mirrored).
*Accept:* the demo matrix is legible at a glance; `PROVIDER_ERROR`, `NOT_FOUND` and "state preserved,
last refresh failed" read differently; no `demo://` value appears in any `href` anywhere in the DOM.
*Tests:* `pnpm lint && typecheck && build` plus a Playwright rehearsal asserting the no-`href` property.

### V2-F — Channel-specific outreach + guardrails → `claude/v2-f-channel-outreach`
`personalize` emits one draft per eligible channel; `OutreachDraft` channel enum, nullable subject,
`content_hash`; the two modified review checks; `OutreachViewer` grouped by channel.
*Accept:* email drafts for the canonical companies byte-identical to v1; LinkedIn drafts additive; still
exactly seven checks. *Tests:* `test_review.py` extended — a draft body containing a foreign email
address hard-fails; a LinkedIn draft with no subject passes; the `[Your Name]` regression still fails.

### V2-G — Gmail OAuth (connection only, no sending) → `claude/v2-g-gmail-oauth`
`oauth_states`, `gmail_connections`, the Fernet token store, connect/callback/disconnect, operator
gating, `state` + PKCE, `connected_account_identifier()`, an operator settings panel showing the
connected address.
**Hard gate (§3.3):** verify against live Google documentation and a real consented test account whether
`messages.list(labelIds=["SENT"])` and `messages.get(format="metadata")` are permitted under
`gmail.metadata`. Record in `PROGRESS.md`. Do not request `gmail.readonly`.
*Accept:* connect/disconnect round-trips; a replayed or expired `state` is rejected; the refresh token is
never readable from any endpoint or log; `connected_account_identifier()` returns the address and never
a credential; the scope finding is recorded.
*Tests:* `test_gmail_oauth.py`.

### V2-H — Action proposal + human approval *(Demo executor only)* → `claude/v2-h-action-approval`
`action_proposals` (with `sender_identifier`), extended `approvals`, `action_events`, the
proposal/approve/reject/execute endpoints, `DemoEmailSendProvider`, mode-aware
`require_action_capability()`, `DEMO_MAX_ACTIONS_PER_RUN`, an approval UI showing the content hash and
the sending identity, a third "Outreach" tab on the run page.
*Accept:* an approved proposal executes in Demo and writes a `demo://` execution with the synthetic
sender; editing the draft after approval yields `409 CONTENT_CHANGED`; a `MISMATCH` LinkedIn profile is
blocked with a visible reason and no override affordance; a demo action succeeds with no operator session
while a live action is refused.
*Tests:*
- `test_request_idempotency.py` *(mechanism A, independently, BOTH origins)*.
- `test_recipient_send_policy.py` *(mechanism B, independently, LIVE-scoped)* — Demo/Demo never blocks;
  Live/Live blocks on the second send; a Demo execution never blocks a later Live send; `FAILED` frees
  the identity; `UNCERTAIN`/`ABANDONED` continue to block it.
- `test_execution_origin_binding.py` — origin is never settable from a request body.
- `test_action_policy_integration.py`; `test_content_hash_invalidation.py`;
  `test_action_authorization.py` (all five live gates, each bypassed in turn);
  `test_send_provider_mode_binding.py` (both directions).

### V2-I — Live Gmail execution + reconciliation + audit → `claude/v2-i-gmail-execution`
`GmailSendProvider`, the claim/lease/dispatch path, the §3.4 classifier, the §3.3 bounded reconciliation
loop with its call budget, the stale-claim sweep, `ABANDONED`, the audit read endpoint, the execute-time
sender re-verification.
**Hard gate:** confirm documented Gmail semantics for each 5xx before moving any out of
`ACCEPTANCE_UNKNOWN`; if V2-G found metadata-only reconciliation unviable, present the `gmail.readonly`
trade to the user as an explicit decision.
*Accept:* a simulated acceptance-unknown resolves to `SUCCEEDED` when findable within bounds and stays
`UNCERTAIN` when not; `NOT_FOUND_WITHIN_BOUNDS` never yields `FAILED`; a duplicate execute never produces
a second send; a crash between `CLAIMED` and settle recovers to `UNCERTAIN`; an `ABANDONED` execution
still blocks the recipient; disconnecting and reconnecting Gmail to a different account blocks an
already-approved proposal with `sender_changed`. One real manual send, recorded.
*Tests:* `test_send_failure_taxonomy.py`; `test_reconciliation_bounds.py`; `test_sender_binding.py`;
`test_execution_recovery.py`; `test_action_audit_trail.py`.

### V2-J — Quality, metrics, production, v2 release → `claude/v2-j-quality-release`
`/evaluation` extensions; the redaction rule; **the I2 backlog** — re-verify `docs/DEPLOYMENT.md` against
the real Render/Neon setup, production proxy validation, the per-IP-rate-limiting-behind-the-proxy
design, `make search-smoke`; remove the stale `NEXT_PUBLIC_API_URL` block from the root `.env.example`;
document every new env var and its failure mode; full suite green on SQLite **and** Postgres. Then the
single integration PR `feature/v2-contact-enrichment → master`, the Neon `production` migration, and tag
`v2.0.0`.

**Ordering note:** V2-D is the only credential-dependent checkpoint. Because the provider boundary is
settled in V2-C, D can slip after F or G without blocking anything.

---

## Part 14 — Demo action gating: A vs B — Option B CONFIRMED

**Option A** — operator-gated Demo action execution. **Option B** — public Demo simulated execution
under bounded Demo controls; Live execution strictly operator-only.

| Dimension | A | B |
|---|---|---|
| Security surface | Nil beyond A's own gate | `DemoEmailSendProvider` has zero network egress by construction; ids are validator-enforced `demo://`; the sender is `@groundwork.invalid` (IANA-reserved, unresolvable); the provider is bound by run mode so a demo run cannot reach `GmailSendProvider`. Residual surface is *database writes* — the same class public demo runs already have. |
| Abuse risk | None | Bounded by the existing public-write limiter, `DEMO_MAX_ACTIONS_PER_RUN`, and small rows. Comparable to the already-accepted public run-creation surface. |
| Portfolio UX | A visitor can research → qualify → enrich → review, then hits a login wall at the one screen that demonstrates v2's entire thesis. | The complete flow is walkable end to end by anyone with the link. |
| Architectural simplicity | Breaks I1's "Demo Mode must never gain an operator-session dependency", and makes demo prospect-approval public while demo action-approval is not. | Preserves the invariant. One gating idiom, not two. |
| Live gating | Unchanged | Unchanged — five gates. |

**Option B is CONFIRMED**, on four conditions: (1) Live gating untouched; (2) the sender bound by run
mode and asserted by test in both directions; (3) demo action endpoints Origin-checked and rate-limited
plus `DEMO_MAX_ACTIONS_PER_RUN`; (4) connecting/disconnecting Gmail stays operator-only in both modes.

B is simultaneously the better product *and* the simpler architecture: A buys no real security (the demo
sender cannot reach the network and its address cannot resolve) at the cost of a broken invariant, a
second gating idiom, and a wall in front of the feature.

---

## Part 15 — Risks & open questions

1. *Canonical-demo drift.* V2-F changes what `personalize` emits. Mitigation: assert byte-identical
   email drafts and unchanged statuses/scores/verdicts at every gate.
2. *Apollo response shape unverified.* The status map and `asserted_company_domain` availability are
   assumptions until V2-D. Mitigation: unmapped status ⇒ `UNVERIFIED` (never sendable); §3.7's
   precedence order works with the domain absent.
3. *`gmail.metadata` may not support even a bounded `SENT` scan.* The largest open technical unknown.
   Mitigation: the V2-G hard gate verifies it before V2-I depends on it; the fallback is an explicit,
   evidence-backed scope decision. If neither works, `UNCERTAIN` is permanent — honest degradation.
4. *Reconciliation indexing lag.* Absence within bounds is not proof. Mitigation: the type says so
   (`NOT_FOUND_WITHIN_BOUNDS`); no code path converts it to `FAILED`.
5. *5xx classification.* Definitive would risk a double send; unknown risks a stuck `UNCERTAIN` that
   permanently blocks a recipient. Mitigation: fail safe to `UNCERTAIN`.
6. *Check #2 goes live.* Mitigation: V2-B lands the rewritten check's unit tests before V2-C writes an
   identifier.
7. *Partial-index drift false positive.* Alembic's `compare_metadata` may disagree textually with
   Postgres's reflected predicate. Mitigation at V2-B: match Postgres's canonical rendering rather than
   excluding the index from drift detection.
8. *Over-blocking from email casefolding.* Accepted deliberately: for a safety rule, over-blocking is a
   visible annoyance and under-blocking is a duplicate email to a real person.
9. *Scope.* Ten checkpoints is roughly twice v1's build. Mitigation: the Demo path is complete after
   V2-H; V2-I/J are the "real send" tier and can be cut without leaving anything half-built.

**Open questions for the user.**
- **Which Gmail sending identity/domain?** Determines the `Message-ID` host and the `From`. Needed at
  V2-I; nothing earlier is blocked. This is the **only** user-level open question remaining — it does
  not affect `sender_identifier`'s design, which is whatever account is connected, resolved at runtime.
- ~~Confirm Option B for demo action gating~~ — **resolved: Option B is CONFIRMED** (Part 14).

---

## Part 16 — Preserved invariants checklist

Everything below must survive every future revision:

- `PASS` is a hard floor for EMAIL_SEND; `VERIFIED` is the only sendable email verification state; no
  override path exists anywhere in v2.
- Five independent contact axes, never collapsed.
- Enrichment never writes `Contact.verification` (C3 — it would move every ICP score).
- No LLM-authored identifiers; no LLM identity matching; no LLM in the review path.
- Last-known-good enrichment: a failed attempt never overwrites a derived provider-backed state.
- Post-dispatch ambiguity ⇒ `UNCERTAIN`; never an automatic resend.
- Request idempotency binds in both modes; the recipient-level cross-run rule binds `LIVE_EXTERNAL`
  only. A Demo execution never consumes a live recipient identity, never blocks another Demo visitor,
  and never blocks a future Live send; a Live execution can never bypass the rule.
- An `ACTION`-scope approval carries `action_proposal_id`, `content_hash` **and** `hash_version`; a
  version mismatch supersedes it rather than revalidating it.
- `sender_identifier` is canonical at every persistence layer; the connection row keeps the provider
  display form.
- Least-privilege Gmail scope with a hard gate before any escalation.
- No LinkedIn SEND executor — `ActionType` has no such member.
- Public, zero-egress Demo simulated actions (Option B, CONFIRMED); strict operator gating for Live
  actions.
- Per-checkpoint branches → PRs into `feature/v2-contact-enrichment`; `master` untouched until V2-J;
  Render deploys `master` only; Neon `production` never migrated during v2 development.
- Canonical v1 Demo regression invariants: statuses, scores, review verdicts, evidence counts and
  `target_count = 7` byte-identical at every phase gate.
- Exactly seven review checks.
- Zero paid provider calls in CI; every smoke script gated by `--i-understand-this-costs-money`.
- `domain/` stays pure; `providers/` never import repositories; `engine/*::call_*()` are the only
  telemetry-persistence seams; `redact()` is the only error-string choke point.

---

## Verification procedure (for the implementing sessions, not V2-A)

- `make test` green on SQLite; the same suite green against the Postgres service container in CI.
- `alembic upgrade head` on the Neon `v2-development` child branch; `test_migration_drift.py` green with
  the partial unique index present.
- `make demo-reset && make dev`; walk `docs/DEMO_SCRIPT.md` plus the new enrichment and action beats;
  diff the board against the `v1.0.0-production` tag.
- Zero paid provider calls in CI; every smoke script guarded and never invoked by `make test`.
- Every checkpoint merges via a PR into `feature/v2-contact-enrichment`; `master` is touched once, at
  V2-J.

**Files changed at V2-A: documentation only — this file, `docs/ARCHITECTURE.md`, `docs/PROGRESS.md`,
`CLAUDE.md`. Zero application code, zero migrations, zero provider calls.**
