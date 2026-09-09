# Deployment

**A real deployment exists in production**: frontend and API on Render (as two separate
`*.onrender.com` services — no custom domain), Postgres on Neon. This was provisioned outside any
committed session, so most of this document was written before it existed and describes what a
deployment *would* need, not a verified account of what's actually configured on Render/Neon today — a
future session should treat the specifics below (env var values, CORS origins, etc.) as needing a
re-verify-against-the-real-services pass, not as ground truth. Checkpoint I1 made the codebase
deployable (Postgres support, migrations, an operator gate, packaging, CI). Checkpoint I2 is closing the
gaps that only show up once something is actually deployed — starting with the same-origin API proxy
below, added specifically because the frontend and API being separate origins broke the operator session
cookie's intended topology.

---

## Same-origin API proxy (Checkpoint I2)

The frontend (`groundwork-web-febu.onrender.com`) and API (`groundwork-api-iu17.onrender.com`) are two
separate origins, and this project is deliberately not buying a custom domain. That's a problem
specifically for the operator session cookie (`apps/api/groundwork/api/routers/operator.py`): it's
host-only (no `Domain=` attribute), Secure, HttpOnly, SameSite=Lax — correct for a single-site session,
but the browser was previously calling the API's origin directly, which is cross-site from that cookie's
point of view even though both services are one product. Demo Mode (no cookie) already worked
end-to-end; Live Mode's operator session was the specific thing broken by this topology.

The fix is a same-origin proxy (BFF) in the Next.js app: `apps/web/app/api/[...path]/route.ts`, a
catch-all Route Handler. The browser only ever talks to
`https://groundwork-web-febu.onrender.com/api/...`; the route handler forwards each request
server-to-server to the real API, named by a new **server-only** env var, `GROUNDWORK_API_ORIGIN`
(never `NEXT_PUBLIC_`-prefixed, read only at request time inside the handler — the API's real URL can
change without a frontend rebuild). It forwards method/path/query/body/Content-Type/Accept/Cookie/
Origin, relays every `Set-Cookie` value individually (via `Headers.getSetCookie()`, not a generic header
copy that would comma-join them), strips hop-by-hop headers (`Connection`, `Keep-Alive`,
`Transfer-Encoding`, `Upgrade`, `Proxy-Authenticate`, `Proxy-Authorization`, `TE`, `Trailer`) plus
`Content-Encoding`/`Content-Length` (undici already decoded any compressed upstream body before the
proxy re-wraps it), never forwards the browser's `Host` header, and pipes SSE responses through as a
live `ReadableStream` — never buffered — with the outbound `fetch`'s `signal` wired to the incoming
request so a dropped/reconnected SSE connection (the normal case for `lib/useRunStream.ts`) actually
cancels the upstream request rather than leaking it.

Nothing about the API's own auth model changed: `CORS_ORIGINS`/`TRUSTED_HOSTS`/the operator session
signing key/`require_allowed_origin`'s CSRF check are all untouched, and didn't need to be — the proxy
relays the same `Origin` header a same-origin browser request already sends, which was already in
`CORS_ORIGINS` (it had to be, for the pre-proxy cross-origin+credentials setup to have worked at all).

**Known limitation**: the API's rate limiters (`operator.py`'s login limiter,
`api/rate_limit.py`'s public-write/preview limiters) key on `request.client.host`. Once every browser
request is relayed through the Next.js server, the API sees the proxy's outbound IP for all traffic
instead of each visitor's real IP — per-IP rate limiting degrades to one shared bucket. Not fixed here
(it would mean forwarding a client-IP header from a trusted single hop and teaching the API to trust it
only from that hop); see `docs/PROGRESS.md`'s "What Checkpoint I2 added" for the full writeup.

**Deploying/updating this**: set `GROUNDWORK_API_ORIGIN` on the **frontend** Render service to the API's
public URL (`https://groundwork-api-iu17.onrender.com`) — server-side env var, not build-time, so no
rebuild is required if the API's URL ever changes. No env var changes are needed on the API service
itself for this specific change.

---

## Why Postgres, and why SQLite stays for local dev

SQLite (WAL mode) is correct for local development: zero setup, a single resettable file,
`make demo-reset` in under a second. It stops being correct the moment more than one process needs to
write concurrently — SQLite's single-writer lock is a hard ceiling, not a tuning knob, and any real
deployment target (a managed platform restarting your process, a second instance during a rolling
deploy) creates exactly that situation even at this app's small scale.

Postgres removes that ceiling and is what `DATABASE_URL` now accepts as a first-class alternative (see
`groundwork/db_url.py::normalize_database_url` — accepts `postgres://`, `postgresql://`, or
`postgresql+asyncpg://`, normalizes `sslmode`/`channel_binding` query params into asyncpg connect
kwargs). **SQLite is not being replaced** — it remains the `make dev` default and the only thing CI's
SQLite job exercises; Postgres is additive, proven correct by running the identical test suite against
both dialects (`tests/dialect_helpers.py`, `GROUNDWORK_TEST_POSTGRES_DSN`).

## One instance, one worker — deliberately, not as a placeholder

Everything in I1 is scoped to exactly one running process:

- The in-process rate limiters (`groundwork/api/rate_limit.py`) hold state in a module-level dict —
  correct for one process, silently too permissive across N processes.
- The execution lease (`executor_id`, heartbeat, reaper) recovers a *crashed* process's runs; it does
  not coordinate *concurrent* processes beyond not letting them stomp on each other's terminal state.
- `LIVE_MAX_ACTIVE_RUNS`/`LIVE_DAILY_RUN_ALLOWANCE` count rows in the shared `runs` table, so they *do*
  stay correct across multiple processes — but the rate limiters above do not, and that asymmetry is
  worth remembering before scaling out.

The Dockerfile's `CMD` hardcodes `--workers 1` for this reason. **Horizontal scaling (more than one
process/instance) is explicitly out of scope for I1 and is not claimed anywhere in this codebase** —
doing it safely means moving the rate limiters to something shared (Redis, or the DB itself) first.
That's I2 work, not a bug in I1.

## Migration strategy

- **SQLite** (local dev only): `create_all_if_sqlite()` runs at API startup — creates missing tables,
  never alters an existing one. There is no Alembic migration history for SQLite and there shouldn't
  be; a schema change during development means `make demo-reset`.
- **Postgres**: managed exclusively through Alembic. `alembic/env.py` reads `DATABASE_URL` through the
  same `normalize_database_url()` the app itself uses (or an explicit override via
  `alembic upgrade head -x database_url=...`), so migrations always target the same normalized DSN the
  API would connect to. The one committed migration (`alembic/versions/38cbecdcd585_initial_schema.py`)
  was autogenerated from `Base.metadata` and is drift-tested in CI against a real Postgres service
  container via SQLAlchemy's `compare_metadata` — a schema change that isn't matched by a new migration
  fails CI, not production.
- **Before a real deployment's process starts**: run `alembic upgrade head` against the target
  Postgres database as an explicit, separate step — never automatically inside `lifespan()`. `GET
  /api/ready` reports `checks.schema: "behind"` (503) if this was skipped; it never runs the migration
  itself.
- **No automated retention/pruning** exists for `run_events`/`llm_calls`/`search_calls` — an
  append-only history that grows unboundedly. Fine at prototype scale; a real deployment running for
  months would need a retention policy, not built here.

## Public Demo Mode, operator-gated Live Mode

The product is designed to be shown to strangers safely:

- **Demo Mode** requires no authentication and makes no external network call — anyone who can reach
  the deployment can run it, by design.
- **Live Mode** (real OpenAI + real Tavily spend) requires an operator session cookie *in addition to*
  both provider keys being configured. Set `OPERATOR_PASSPHRASE` and `SESSION_SIGNING_KEY` (a long
  random string — `openssl rand -hex 32`) to enable it; leaving either unset means Live Mode is
  hard-disabled regardless of provider keys, never a silent fallback. `SESSION_SIGNING_KEY_OLD` lets a
  key rotation not immediately invalidate live sessions.
- Provider API keys (`OPENAI_API_KEY`, `TAVILY_API_KEY`) are read **only** by the API process from its
  own environment. Nothing in `apps/web` ever sees them — no `NEXT_PUBLIC_`-prefixed provider key
  exists or should ever be added; `GET /api/settings/providers` reports `configured: bool` only.

## Environment variables

The full, current list — every field with its default and a one-line explanation — lives in
`.env.example` at the repo root (API) and `apps/web/.env.example` (frontend). Do not duplicate that
list here; it will drift. The categories worth calling out explicitly:

- **Required for Live Mode**: `OPERATOR_PASSPHRASE`, `SESSION_SIGNING_KEY`, `OPENAI_API_KEY`,
  `TAVILY_API_KEY`. All four, or Live Mode stays unavailable.
- **Required for Postgres**: `DATABASE_URL` pointed at the managed instance. `DB_POOL_SIZE`/
  `DB_MAX_OVERFLOW` are sized for one instance/one worker — see above.
- **Everything else** has a safe default and exists to tune a hard bound (cost, concurrency, timeout),
  not to unlock a feature.

## Frontend

`apps/web` is a plain Next.js app, hosted on Render as a Node service (`next start`, not a static
export) — it has to be a Node runtime, not static hosting, for `app/api/[...path]/route.ts`'s Route
Handler proxy (above) to run at all. As of Checkpoint I2 the browser never reads a build-time API URL:
`GROUNDWORK_API_ORIGIN` (server-only, not `NEXT_PUBLIC_`-prefixed) is read at request time inside the
proxy, so the API's real URL can change without a frontend rebuild — this replaces the old
`NEXT_PUBLIC_API_URL` (inlined at `next build` time), which I1 used before the proxy existed.
`engines.node >= 20.9.0` (see `apps/web/package.json`/`.nvmrc`) matches what Next.js 16 requires.

## What a real deployment still needs

Most of this is now done (Render + Neon, provisioned outside any committed session — see the top of
this document). Kept here, marked, so a future session doesn't have to re-derive what's left:

1. ~~Provision Postgres~~ **done** — Neon. Confirm `alembic upgrade head` has actually been run against
   it (`GET /api/ready`'s `checks.schema` reports this) — not independently re-verified by this session.
2. ~~Provision an API host~~ **done** — Render (`groundwork-api-iu17.onrender.com`). Confirm
   `DATABASE_URL`/`OPERATOR_PASSPHRASE`/`SESSION_SIGNING_KEY`/provider keys are set as Render secrets,
   not re-verified by this session.
3. ~~Provision a frontend host~~ **done** — Render (`groundwork-web-febu.onrender.com`). **New as of
   I2**: set `GROUNDWORK_API_ORIGIN` on this service to the API's URL — see "Same-origin API proxy"
   above.
4. **No custom domain** (a deliberate decision, not a gap — see "Same-origin API proxy" above for how
   I2 closed the operator-cookie problem without one). Confirm `CORS_ORIGINS` on the API includes
   `https://groundwork-web-febu.onrender.com` and `TRUSTED_HOSTS` includes the API's own Render
   hostname — this session did not have access to the real Render dashboard to verify these directly;
   Demo Mode already working end-to-end in production is evidence CORS is at least permissive enough for
   that traffic, not proof every setting is exactly as documented here.
5. **A decision on horizontal scaling** — unchanged from I1: if more than one API instance is ever
   needed, the in-process rate limiters must move to shared state first (see "One instance, one worker"
   above). The I2 proxy adds a related, smaller wrinkle: it also collapses per-IP rate limiting to one
   shared bucket regardless of instance count, because the API now sees the proxy's IP for all browser
   traffic — see "Same-origin API proxy" above.
6. **A decision on secrets management** for Render specifically — not verified by this session.
7. **`scripts/prod_smoke.py`** (author-only — see its own docstring) is still the intended first thing
   to run against the real deployment to verify `/api/health`/`/api/ready` and a real Demo Mode run
   end-to-end. As of V2-J it also asserts `/evaluation` carries the `enrichment`/`actions` blocks and
   basic internal consistency between their fields — still Demo-only, still zero paid provider calls.
   Not run by this session (out of scope — no real Live Mode call, no direct access to the
   production URLs beyond what was given in the task).

## V2-J — production rate limiting (documentation-only for v2.0.0)

No new rate-limiting code shipped in V2-J. This section records the real, already-shipped behavior and
its known gap so a future session implementing the deferred redesign doesn't have to re-derive it.

- **BFF/shared-bucket behavior.** Every rate limiter in this codebase (`api/rate_limit.py`'s
  `SlidingWindowRateLimiter` — public-write, preview, operator-login, and the V2-H/I action-write
  limiter) is in-process, per-`request.client.host`. The I2 same-origin proxy (`apps/web/app/api/
  [...path]/route.ts`) is a Backend-For-Frontend: it forwards every browser request server-to-server, so
  the API sees the *proxy's* IP for all browser traffic, not each individual visitor's. This collapses
  every browser-driven caller onto ONE shared bucket per limiter, regardless of how many distinct real
  visitors are hitting it — already true since I2 shipped, not a V2-J regression, but worth stating
  plainly rather than leaving implicit.
- **The Uvicorn/Render layer is INFERRED, not proven.** This session has no direct access to the real
  Render dashboard or its network configuration. It is standard for a platform like Render to sit a
  request through its own edge/load-balancer before an app's Uvicorn process ever sees it, and Render is
  documented to set `X-Forwarded-For` there — but this codebase does not independently verify that for
  this specific deployment, and `request.client.host` as FastAPI/Uvicorn resolves it depends on how (or
  whether) `--proxy-headers`/`--forwarded-allow-ips` are configured on the Uvicorn process, which this
  session did not inspect. Treat "the API sees the proxy's IP, and the proxy's IP is the only thing
  worth rate-limiting on today" as the safe, conservative reading until a future session confirms the
  Uvicorn layer's exact trust configuration against the real deployment.
- **No blindly trusted `X-Forwarded-For`.** Nothing in this codebase reads `X-Forwarded-For` (or any
  other client-supplied header) to determine the rate-limit key — `_client_key()` (in each router that
  defines one) uses `request.client.host` only, which is whatever the ASGI server resolved as the
  peer address. A malicious client cannot forge a header to evade or frame another caller under today's
  code, precisely because no header is trusted for this purpose at all. The deferred design (below) must
  preserve this property: if it ever does read a forwarded-for style header, it must do so only from a
  layer proven to be trustworthy (the platform's own edge, never an arbitrary hop) — never take the
  client's word for its own IP.
- **Deferred secure BFF-side design.** Not implemented in v2.0.0. The shape sketched for a future session:
  move rate limiting into the Next.js proxy itself (which DOES see each real visitor's IP, since it's the
  first hop after the platform edge), keyed on a header the platform itself sets and the proxy can trust
  (verified against Render's actual documented behavior, not assumed) — and reject any request lacking
  that trusted header once verified, rather than falling back to a single shared bucket silently. This
  was explicitly named in-scope for V2-J's folded-in I2 backlog and explicitly deferred again here: doing
  it correctly requires verifying Render's real edge behavior against a live deployment, which this
  session does not have credentials/access to do, and a *wrong* trust decision here (trusting a header an
  attacker can set) is worse than today's coarser-but-safe shared-bucket behavior.
- **Production rate-limit saturation experiment** (deliberately NOT performed during this implementation
  session, per the V2-J task brief) — a future session with real deployment access should exercise each
  limiter against the live Render/Neon stack to confirm the shared-bucket behavior above matches what's
  actually observed, before relying on it as a security control rather than an abuse-shape mitigation.

## V2-J — release/rollback sequence for `v2.0.0`

Recorded here so the eventual integration session (not this one — this session does not merge, migrate,
tag, or deploy anything) has a concrete checklist rather than reconstructing it under pressure:

1. Confirm the single integration PR `feature/v2-contact-enrichment -> master` is green on every required
   CI job (Backend SQLite, Backend Postgres + migration drift, Frontend lint/typecheck/test/build, API
   Docker build) — the same bar every per-checkpoint PR into `feature/v2-contact-enrichment` already
   cleared, now against `master`.
2. **Migration-before-serve.** `alembic upgrade head` against the real Neon `production` database MUST
   complete successfully BEFORE the new API image is allowed to serve traffic — this is the same
   ordering V1's own deployment already required (see "Migration strategy" above); V2-J does not relax
   it, and no V2-J commit ships a migration of its own (there is no schema change in this checkpoint) —
   the requirement is restated here only because the *next* session's migration (V2-B through V2-I's
   accumulated schema, if it hasn't already been applied to `production`) must still follow it.
3. Deploy the API, then the frontend (the frontend's same-origin proxy depends on the API being reachable
   and correctly versioned; the reverse ordering risks the frontend serving before the API it proxies to
   is ready).
4. Verify with `scripts/prod_smoke.py --base-url <production-url> --i-understand-this-targets-a-real-
   deployment` — all four checks (health, ready, a real Demo run, and V2-J's `/evaluation` shape check)
   green.
5. Tag `v2.0.0` on the exact commit that was actually deployed (not the merge commit's parent, not a
   later commit) — only after step 4 passes.
6. **Rollback**, if any of the above fails after partial completion: redeploy the previous known-good API
   image/commit (Render supports redeploying a prior build); the schema migration in step 2 is additive
   only (V2-B through V2-I never drop/retype a v1 column or table — see CLAUDE.md's invariants), so a
   rollback to the pre-V2 API image continues to work against the post-migration schema unmodified. Never
   attempt an Alembic downgrade against `production` as part of a rollback — the additive-schema
   guarantee already makes that unnecessary, and a downgrade is a strictly riskier operation than simply
   redeploying an older, compatible image.
