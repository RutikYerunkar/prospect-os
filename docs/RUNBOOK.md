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
