# Runbook

Operational procedures for the one process this prototype runs as. See `docs/DEPLOYMENT.md` for what a
real deployment target still needs; this document assumes the process is already running somewhere
(locally, or a future I2 host) and something needs investigating or fixing.

---

## Health vs. readiness — which one to page on

- **`GET /api/health`** — process liveness only. Never touches the database or a provider. If this
  doesn't respond, restart the process; nothing else here applies.
- **`GET /api/ready`** — can this process serve real traffic right now? Runs a real `SELECT 1` and
  (Postgres only) an Alembic schema-currency check. Returns `503` with a `checks` object naming what's
  wrong (`database: "unreachable"` or `schema: "behind"`) rather than a bare status code. **A `503`
  here does not mean the process is broken** — it means don't route new traffic to it yet (a slow
  Postgres blip, a skipped migration). Restarting a process that's unready for schema reasons will not
  fix it; running the migration will.

## Startup / shutdown behavior

On startup, the process:
1. Runs `create_all_if_sqlite()` (SQLite only — a no-op against Postgres).
2. Mints a fresh `executor_id` (UUID) for this process.
3. Reaps any `RUNNING` run whose heartbeat is older than `EXECUTOR_STALE_THRESHOLD_S` (default 60s) —
   i.e. runs that were genuinely abandoned by a previous process, not runs a fast-restarting *healthy*
   process still owns.
4. Starts the background reaper loop (repeats step 3 every `EXECUTOR_REAPER_INTERVAL_S`, default 30s).

On shutdown (SIGTERM/lifespan exit), the process:
1. Cancels the reaper loop.
2. Waits up to `SHUTDOWN_DRAIN_TIMEOUT_S` (default 20s) for in-flight runs to finish naturally.
3. Force-interrupts anything still `RUNNING` and still owned by *this* process's `executor_id` after
   that window.
4. Closes provider runtimes and disposes the DB engine.

**No run is ever auto-resumed after an interruption, by design.** An `INTERRUPTED` run is a terminal
state; starting it again means creating a new run through the normal API. If you need a run to survive
a deploy, that's a reason to keep `SHUTDOWN_DRAIN_TIMEOUT_S` generous for your run durations, not a
reason to expect this system to resume it.

## A run looks stuck

1. `GET /api/runs/{id}` — check `status`. `RUNNING` past its expected duration is the symptom; the
   run-level wall-clock watchdog (`RUN_WALL_CLOCK_TIMEOUT_S`/`LIVE_RUN_WALL_CLOCK_TIMEOUT_S`) should
   have already moved it to `PARTIAL`/timed out individual prospects — if it hasn't, the process itself
   may be wedged (check `/api/health`).
2. Check the structured logs (JSON, one line per event) for this `run_id` — every log line that has
   run-level context carries `"run_id"` as a top-level field, so `grep '"run_id": "<id>"'` (or your log
   platform's equivalent field filter) surfaces the whole story: heartbeat-refused warnings mean this
   process lost the lease (another process's reaper reclaimed it, or this process itself was slow past
   `EXECUTOR_STALE_THRESHOLD_S`); provider warnings (`llm call failed`/`search call`) show provider-side
   trouble.
3. If the owning process crashed outright (no clean shutdown), the *next* process to start reaps it
   automatically within `EXECUTOR_REAPER_INTERVAL_S` of that process starting — no manual DB surgery
   needed. If you must intervene sooner, the guarded pattern the code itself uses is: only transition a
   `RUNNING` row to `INTERRUPTED` when its `heartbeat_at` is actually stale — don't hand-edit `runs.status`
   without checking that first, or you can race a still-healthy process's own finalize.

## Schema drift / migration problems

- `GET /api/ready` returning `checks.schema: "behind"` (Postgres only) means `alembic upgrade head`
  hasn't been run against this database since the code was last deployed. Run it — this is always safe
  to run unconditionally (`make db-upgrade`; verified idempotent — running it twice is a no-op the
  second time, see `test_alembic_upgrade_head_is_idempotent`).
- `make db-current` shows the database's current revision; `make db-history` shows the full migration
  chain.
- A local SQLite file predating a `models/tables.py` change (missing a column `create_all()` doesn't
  retroactively add): `make demo-reset` — deletes and recreates the file. Never attempt this against a
  real Postgres database; that's what Alembic migrations are for.
- If `alembic upgrade head` itself fails partway on Postgres: do not manually drop tables to "start
  over" on anything but a disposable/test database — diagnose the specific migration step first
  (Alembic transactions are per-migration, so a partial failure leaves the DB at a known prior
  revision, visible via `alembic current`).

## Log format

Every log line is one JSON object (`groundwork/logging_config.py`): `timestamp`, `level`, `logger`,
`message`, `environment`, plus whichever of `request_id`/`run_id`/`prospect_id`/`executor_id`/
`latency_ms` were available at that call site. Every message and any exception traceback is passed
through the same redaction (`observability/redact.py`) used before anything is written to the
database — a secret should never appear in logs even from an un-redacted upstream error string, but
redact at the source first; this is a safety net, not the primary control.

## Rotating `SESSION_SIGNING_KEY`

1. Move the current `SESSION_SIGNING_KEY` value into `SESSION_SIGNING_KEY_OLD`.
2. Set `SESSION_SIGNING_KEY` to a new value (`openssl rand -hex 32`).
3. Deploy. Existing operator sessions signed with the old key still verify (checked as a fallback);
   newly issued sessions use the new key.
4. After `SESSION_MAX_AGE_S` (default 12h) has fully elapsed since the rotation, `SESSION_SIGNING_KEY_OLD`
   can be cleared — every session that could have been signed with it has expired by then.

## Rate limiting is per-process, not global

`groundwork/api/rate_limit.py`'s sliding-window limiters (operator login, public writes, play preview)
hold their state in that one process's memory. If you're running more than one instance behind a load
balancer (not the I1 default — see `docs/DEPLOYMENT.md`), each instance enforces its own independent
limit; the effective limit across the fleet is `N × configured limit`, not the configured limit. This
is a known, documented gap, not a bug to chase — closing it means moving that state somewhere shared,
which is I2 scope.

## Rotating `TOKEN_ENCRYPTION_KEY` (V2-G — Gmail refresh token at rest)

1. Move the current `TOKEN_ENCRYPTION_KEY` value into `TOKEN_ENCRYPTION_KEY_OLD`.
2. Set `TOKEN_ENCRYPTION_KEY` to a new Fernet key:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
3. Deploy. The one `gmail_connections` row's already-encrypted refresh token still decrypts (current
   key tried first, then `TOKEN_ENCRYPTION_KEY_OLD` — mirrors `SESSION_SIGNING_KEY`'s rotation exactly,
   see above); any *new* connect always encrypts with the new current key.
4. `TOKEN_ENCRYPTION_KEY_OLD` can be cleared once the operator has reconnected Gmail at least once
   since the rotation (a fresh connect re-encrypts under the new key) — there is no TTL to wait out
   here, unlike session cookies, since there's only ever one connection row.

**Load-bearing dependency, recorded rather than silently relied on:** the OAuth callback (`GET
/api/gmail/callback`) is a cross-site top-level GET — Google redirects the browser back to this app's
own origin, and the operator session cookie must still be sent on that request for the state/session
binding check (§ CLAUDE.md's v2 invariants) to succeed. This only works because the operator cookie is
`SameSite=Lax` (`api/operator_auth.py`, unchanged by V2-G) — `SameSite=Lax` allows a cookie on a
top-level cross-site navigation; `SameSite=Strict` would not, and would break every real Gmail connect
attempt. Do not tighten the operator cookie to `SameSite=Strict` without redesigning this flow.

**Google OAuth client "Testing" publishing status.** A Google OAuth client left in Testing status may
issue refresh tokens with a limited lifetime (Google's documented behavior names 7 days). This is not a
V2-G blocker — no sending exists yet — but it is a V2-I (live Gmail execution) operational
precondition: the connected refresh token could silently stop working after 7 days on a Testing-status
client, which would surface as a live send failing with an auth error rather than anything V2-G's own
connect/disconnect flow would catch. Move the OAuth consent screen to "In production" (or add the
operator as a test user with awareness of the 7-day limit) before relying on a long-lived connection.

## Live Mode cost/abuse controls, at a glance

- `LIVE_MAX_ACTIVE_RUNS` (default 1) and `LIVE_DAILY_RUN_ALLOWANCE` (default 10) are enforced against
  the shared `runs` table, so these **do** stay correct across multiple processes, unlike the
  in-process rate limiters above.
- Per-call/per-run hard bounds (prospects per run, LLM/search call caps, output token cap) are
  documented in `.env.example` next to each setting and are the real spend control — a soft USD budget
  (`LIVE_RUN_SOFT_BUDGET_USD`) only activates once real pricing is also configured, and is never a hard
  cap even then.
- If Live Mode needs to be disabled entirely without a redeploy: unset `OPERATOR_PASSPHRASE` or
  `SESSION_SIGNING_KEY` (either alone hard-disables it) via whatever mechanism your host uses to change
  environment variables, then restart the process.

## Live `EMAIL_SEND` — the structural refusal is gone; real dispatch is reachable (V2-I-b, post-smoke correction)

`LiveExternalEmailSendDisabled` is no longer on the real dispatch path. **Gate-order history:** an earlier
state of the V2-I-b checkpoint briefly removed this refusal on the reasoning that a documented "Postgres
unreachable in this environment" gap was an acceptable substitute for a passed Postgres verification. It
was not; the user reverted it and required a CI-verification-only PR (#23) instead. That PR's CI came back
green on all four required checks, and the user then gave explicit, separate authorization to remove the
refusal a second time — this is that final state. See `docs/PROGRESS.md`'s V2-I-b entry for the full
three-state account.

**What actually gates a real send now** (the refusal no longer does):
1. `providers/live/gmail_send.py::GmailSendProvider` — the real send/reconcile provider, now reachable.
2. `api/gmail_provider_factory.py::build_gmail_send_provider` — resolves a working connection async;
   returns `None` (never a fixture fallback) on a missing/undecryptable credential, which
   `execute_action` turns into `409 GMAIL_NOT_CONNECTED` with no execution row ever created.
3. `api/live_send_orchestration.py::dispatch_live_email_send` — the real dispatch ordering (CLAIMED ->
   pre-dispatch Gmail history checkpoint acquired+persisted -> allowance reservation -> guarded IN_FLIGHT ->
   the one Gmail HTTP call -> classify -> settle). The checkpoint step (V2-I-b correction, post-smoke) fails
   closed — settles `FAILED`/`PROVEN_NOT_DISPATCHED`, touches no allowance, calls Gmail no further — if
   acquisition or persistence fails.
4. The rolling-24h send allowance (`LIVE_MAX_SENDS_PER_DAY`, default 50) — DB-backed, correct across
   processes; a `409` with `send_allowance_exhausted` in `blocked_reasons` at the policy pre-check, or a
   denied reservation at the transaction itself for a narrower race — both block before any Gmail call.
5. Recipient send-suppression (local `contact_channels` + global `email_suppressions`,
   `recipient_suppressed`) — see the section below; still blocks both Demo and Live, no override.
6. The five server-side Live authorization gates unchanged since V2-H (operator session, approval exists,
   sender unchanged since approval, content unchanged since approval, a fresh policy re-evaluation) — all
   still enforced, all still independent of the refusal that used to also be there.
7. Request idempotency (§3.5A) and the recipient-level one-initial-Live-send-per-identity rule (§3.5B) —
   both unchanged.

`resolve_send_provider(Mode.LIVE)` and the `LiveExternalEmailSendDisabled` exception are still present in
the codebase, unchanged, and still unconditionally raise if anything calls that function with `Mode.LIVE` —
they are simply no longer called on the real Live `EMAIL_SEND` dispatch path (`execute_action` now calls
`build_gmail_send_provider`/`dispatch_live_email_send` directly). This is a defensive-only guard against a
future accidental miswire, not the thing enforcing safety here — the controls listed above are.
`test_live_dispatch_reachable.py` proves the successful path reaches Gmail only after every gate above has
passed, and that suppression/allowance/missing-credential each independently block with zero Gmail calls.

- `DEMO_MAX_ACTIONS_PER_RUN` (default 10, V2-H) caps how many `action_executions` rows one Demo run may
  accumulate — a public-abuse control, independent of the per-client-IP `action_write_rate_limit_*`
  settings (mirrors `public_write_rate_limit_*`'s shape, same per-process caveat as above).

A send settles to one of three terminal-or-pending states (§3.4 taxonomy, `domain/send_classifier.py`):
`SUCCEEDED` (Gmail returned 200 with a parseable `Message.id`), `FAILED` (provably never dispatched, or a
definitive 400/401/404 rejection — the ONLY status that frees the recipient identity), or `UNCERTAIN`
(anything else — every 403/429/5xx, a timeout after dispatch began, an unparseable 200). An `UNCERTAIN`
execution is never resent automatically; reconcile it (`POST /api/actions/executions/{id}/reconcile`,
operator-gated) up to `RECONCILE_MAX_ATTEMPTS` times within `RECONCILE_WINDOW_S` of dispatch, or let it
settle to `ABANDONED` once the window closes (also zero-egress — no Gmail call is made once exhausted or
expired). `ABANDONED` is terminal and still blocks the recipient identity; there is no resend anywhere in
this codebase, ever.

**Reconciliation is historyId-anchored, not Message-ID-anchored (V2-I-b correction, post-smoke).** The one
authorized real Gmail smoke succeeded, and a read-only follow-up diagnostic against that exact sent message
found `Message-ID` and `X-Google-Original-Message-ID` BOTH absent from Gmail's `format=metadata` response —
Subject/To/From/Date were present and matched. The original design assumed Gmail preserves a caller-supplied
Message-ID; that assumption is disproven. `GmailSendProvider.find_sent_message` now takes the persisted
`pre_dispatch_history_id` plus the approved Subject/recipient/sender and a dispatch/settle time window,
calls `users.history.list(startHistoryId=..., labelId="SENT", historyTypes=["messageAdded"])` for candidate
ids, then `messages.get(format="metadata", metadataHeaders=[Subject,To,From,Date])` per candidate — still
only `gmail.metadata`-scoped operations, still never `q`, still never body/`full`/`raw`. A candidate matches
only if Subject (canonicalized, RFC-2047-aware), To, From, and Date ALL match; every bounded candidate is
evaluated before deciding (never a first-match short-circuit), so `FOUND` (exactly one match) is
distinguishable from the new `ReconcileStatus.AMBIGUOUS` (more than one match — stays `UNCERTAIN`, never
guesses). A `404` from `history.list` (checkpoint outside Gmail's retention window) is the new
`ReconcileStatus.HISTORY_EXPIRED` — also stays `UNCERTAIN`. `message_id_header` is still generated and
persisted for historical/audit compatibility; the reconciliation path no longer reads it. See
`docs/PROGRESS.md`'s "V2-I-b reconciliation correction" entry for the full account — no real sender/
recipient address, provider message id, raw Message-ID value, token, or secret is recorded there.

**No second real Gmail send has occurred or been authorized.** The one authorized real-Gmail smoke (a
single real send to an operator-controlled inbox) has been performed, under explicit separate
authorization and a strict one-send budget, and succeeded. Any further real send requires its own new,
separate, explicit authorization.

A `CLAIMED`/`IN_FLIGHT` execution stuck past `EXECUTION_STALE_LEASE_S` (default 300s — a process crashed
mid-dispatch) is recovered via `POST /api/actions/executions/{id}/recover` (operator-gated): a stale
`CLAIMED` settles to `FAILED`/`PROVEN_NOT_DISPATCHED` (it's provable nothing was ever sent); a stale
`IN_FLIGHT` settles to `UNCERTAIN` (it's not provable either way) — never dispatched, never re-dispatched,
never releases the allowance reservation.

## Recipient send-suppression (V2-I-a) — what `recipient_suppressed` means

A blocked `EMAIL_SEND` proposal with `recipient_suppressed` in `blocked_reasons` means a provider
(currently Hunter, via HTTP 451) reported a legal/privacy restriction on that email identity — see
`docs/PROGRESS.md`'s V2-I-a entry for the authoritative semantics. Two things worth knowing operationally:
- It is **global and sticky** — once `email_suppressions` carries a normalized identity, every proposal
  targeting that address is blocked, in every prospect and run, forever. There is no clear/override
  endpoint anywhere in this codebase (by design, D7 extended to this axis); the only way to change it is
  a deliberate, reviewed data migration, never a runtime toggle.
- It applies to **both** Demo and Live `EMAIL_SEND` — a Demo walkthrough exercises the real policy, not a
  relaxed one. It does **not** apply to `LINKEDIN_COPY_AND_OPEN`, which has no suppression concept.

## V2-J — reading the enrichment/actions Quality-tab panels

`GET /api/runs/{id}/evaluation` gained two new top-level blocks, `enrichment` and `actions`, computed on
read from `enrichment_calls`/`contact_channels`/`contact_enrichments` and `action_proposals`/
`action_executions`/`action_events`/`approvals` respectively — no new table, no persisted metric, nothing
that can silently drift out of sync with the underlying rows. A few things worth knowing when reading
them operationally:

- **A `null` rate is not zero.** Every `*_rate` field in `enrichment` is `null` when its denominator is
  zero (e.g. `email_verified_rate` is `null` whenever no email was ever FOUND, not `0.0`). The frontend
  renders `null` as "—", never "0%" — treat any observed `0%` in the UI as a REAL zero-rate outcome, not
  an absence of data.
- **`FAILED` is still the only status that frees a Live recipient identity** — restated here because
  `actions.executions_by_status`/`cross_run_recipient_blocks` are the two places this now shows up in
  aggregate: a run whose `executions_by_status` includes several `FAILED` rows for the same recipient and
  zero blocks is expected and correct, not a bug in the metric.
- **`uncertain_count` never means "will retry automatically."** An `UNCERTAIN` execution counted here
  stays exactly as blocking (§3.5B) as any other non-`FAILED` status until an operator reconciles or
  explicitly abandons it (see the reconciliation sections above) — the metric is observability, not a
  queue Groundwork will ever drain on its own.
- **`execution_blocked_attempts` vs `execution_blocked_proposals`.** The former counts every blocked
  execute *attempt* (a caller retrying the same proposal after fixing nothing counts more than once); the
  latter counts distinct proposals. A large gap between them on one proposal is a signal worth
  investigating (a client retry-looping against a permanent block), not itself an error.
- **`cross_run_recipient_blocks` is a subset, not a duplicate, of the ordinary recipient-conflict count.**
  It only counts blocks whose conflicting prior send belongs to a DIFFERENT run — the metric that proves
  the recipient-level rule is doing real cross-visitor/cross-session work, not just protecting a single
  run's own retries. `DEMO_SIMULATED` never contributes to it in either direction (rev 4 — a public demo
  visitor must never block, or be blocked by, anyone else).
- **Proposal-time (`blocked_reasons`) and execution-time (`execution_blocked_reasons`) counters are
  never merged.** A proposal can be created ELIGIBLE and only later blocked at execute time (e.g. the
  approval's `hash_version` went stale) — that shows up in `execution_blocked_reasons` only, never
  retroactively added to `blocked_reasons`.
