import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from groundwork.api import run_service
from groundwork.api.errors import register_error_handlers
from groundwork.api.middleware import MaxBodySizeMiddleware, RequestIdMiddleware
from groundwork.api.routers import evaluation, operator, plays, prospects, runs, settings as settings_router
from groundwork.config import settings
from groundwork.db import SessionLocal, create_all_if_sqlite, engine, schema_upgrade_problems
from groundwork.logging_config import configure_logging
from groundwork.observability.redact import redact
from groundwork.repositories.runs import RunRepository
from groundwork.timeutil import utcnow

# Checkpoint I1 Phase 9C: configured before any logger below actually
# emits anything — nothing imported above logs at pure import time (every
# `logger = logging.getLogger(__name__)` in this codebase is just binding a
# name; the calls happen inside functions, which only run later), so this
# only needs to precede the first real log call, not the imports.
configure_logging()

logger = logging.getLogger(__name__)


async def _reaper_loop(run_repo: RunRepository, executor_id: str) -> None:
    """Periodic companion to the startup reap below — same `reap_stale()`
    call, on `EXECUTOR_REAPER_INTERVAL_S`. Independent task; cancelled
    explicitly at shutdown, never left to be garbage-collected mid-await."""
    try:
        while True:
            await asyncio.sleep(settings.executor_reaper_interval_s)
            stale_before = utcnow() - timedelta(seconds=settings.executor_stale_threshold_s)
            interrupted = await run_repo.reap_stale(stale_before)
            if interrupted:
                logger.warning(
                    "reaper interrupted %d stale run(s): %s", len(interrupted), interrupted,
                    extra={"executor_id": executor_id},
                )
    except asyncio.CancelledError:
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Checkpoint I1 Phase 5: production never runs create_all silently —
    # this only does anything against the SQLite dialect (local dev
    # convenience, disposable file). A Postgres schema is managed
    # exclusively via `alembic upgrade head`, run explicitly before the
    # app starts (see docs/RUNBOOK.md); this just verifies it's current
    # and logs a loud warning if not (GET /api/ready, Phase 9B, is the
    # machine-readable version of the same check).
    created = await create_all_if_sqlite()
    if not created:
        problems = await schema_upgrade_problems(engine)
        if problems:
            logger.warning(
                "database schema is not current — %s (this process will still start; "
                "GET /api/ready will report unhealthy)",
                "; ".join(problems),
            )

    # Checkpoint I1 Phase 4: the ownership-safe execution lease. Minted
    # ONCE per process — every run this process creates/drives stamps its
    # `runs.executor_id` with this value (see `api/deps.py::get_executor_id`,
    # `api/run_service.py`).
    app.state.executor_id = str(uuid.uuid4())
    run_repo = RunRepository(SessionLocal)

    # Startup reap — replaces the old unconditional `sweep_interrupted()`.
    # Marks only genuinely stale RUNNING rows INTERRUPTED (heartbeat older
    # than `EXECUTOR_STALE_THRESHOLD_S`, or never set — a pre-Phase-4 row,
    # or one that crashed before its first heartbeat). A row with a fresh
    # heartbeat is left alone even at startup, so a fast restart racing an
    # already-healthy overlapping process never cuts that process's own
    # live run short.
    stale_before = utcnow() - timedelta(seconds=settings.executor_stale_threshold_s)
    interrupted = await run_repo.reap_stale(stale_before)
    if interrupted:
        logger.warning(
            "startup reap interrupted %d stale run(s): %s", len(interrupted), interrupted,
            extra={"executor_id": app.state.executor_id},
        )

    logger.info("groundwork api starting", extra={"executor_id": app.state.executor_id})

    app.state.reaper_task = asyncio.create_task(_reaper_loop(run_repo, app.state.executor_id))

    # PROCESS-scoped Live Mode runtime (Checkpoint G Phase 5): created once
    # here, closed once at shutdown, never as a hidden module-global. Only
    # constructed when an OpenAI key is actually configured — a public
    # clone with no key runs Demo Mode with zero live-provider machinery
    # touched. Multiple concurrent runs share this one `AsyncOpenAI` client
    # and its `asyncio.Semaphore(LLM_MAX_CONCURRENCY)`.
    app.state.live_runtime = None
    if settings.openai_api_key:
        from groundwork.providers.live.runtime import LiveProviderRuntime

        app.state.live_runtime = LiveProviderRuntime.create(settings)

    # H2: process-scoped Tavily search runtime, same lifecycle discipline —
    # created once here, closed once at shutdown, only when TAVILY_API_KEY
    # is actually configured. Live Mode requires BOTH this and
    # `live_runtime` to be non-null (see `providers/registry.py`).
    app.state.live_search_runtime = None
    if settings.tavily_api_key:
        from groundwork.providers.live.search_runtime import LiveSearchRuntime

        app.state.live_search_runtime = LiveSearchRuntime.create(settings)

    yield

    # --- Shutdown (Checkpoint I1 Phase 4) ---
    # Stop accepting new runs: the ASGI server has already stopped routing
    # new requests by the time a lifespan shutdown begins, so there is
    # nothing further to flip here. Stop the reaper first — no point
    # reaping runs we're about to drain/interrupt ourselves.
    app.state.reaper_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await app.state.reaper_task

    # Drain in-flight runs for a bounded window — most demo/short runs
    # finish well inside it, so this is usually a no-op wait, not a
    # guaranteed full-timeout stall.
    in_flight = [t for t in run_service._background_tasks if not t.done()]
    if in_flight:
        _done, pending = await asyncio.wait(in_flight, timeout=settings.shutdown_drain_timeout_s)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # Anything still RUNNING and owned by THIS executor after the drain
    # window is force-transitioned to INTERRUPTED — guarded by
    # `executor_id` ownership, exactly like the reaper, so a run this
    # process already lost the lease on (e.g. the reaper beat us to it) is
    # never double-transitioned or resurrected. Never auto-resumed later —
    # a rerun is always a new run.
    force_interrupted = await run_repo.interrupt_owned_by_executor(app.state.executor_id)
    if force_interrupted:
        logger.warning(
            "shutdown force-interrupted %d run(s) still owned by this executor: %s",
            len(force_interrupted), force_interrupted,
            extra={"executor_id": app.state.executor_id},
        )

    if app.state.live_runtime is not None:
        await app.state.live_runtime.close()
    if app.state.live_search_runtime is not None:
        await app.state.live_search_runtime.close()
    await engine.dispose()

    logger.info("groundwork api shutdown complete", extra={"executor_id": app.state.executor_id})


app = FastAPI(title="Groundwork API", version=settings.app_version, lifespan=lifespan)

# Middleware executes outermost-last-added-first (Starlette wraps each
# `add_middleware` call around the previous stack) — request-id first (every
# response, including a 400 from any layer below it, gets one), then
# trusted-host, then body-size, then CORS innermost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(MaxBodySizeMiddleware, max_body_size=settings.max_request_body_bytes)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(RequestIdMiddleware)

register_error_handlers(app)

app.include_router(plays.router)
app.include_router(runs.router)
app.include_router(evaluation.router)
app.include_router(prospects.router)
app.include_router(settings_router.router)
app.include_router(operator.router)


@app.get("/api/health")
async def health() -> dict:
    """Process liveness ONLY (Checkpoint I1 Phase 9B) — never queries the
    database or a provider. A process that can answer HTTP at all answers
    this; a load balancer/orchestrator restarting the process on failure
    should key off THIS, not `/api/ready` (which can legitimately be
    unhealthy — a slow migration, a Postgres blip — without the process
    itself being broken)."""
    return {
        "status": "ok",
        "mode": settings.mode,
        "version": settings.app_version,
    }


@app.get("/api/ready")
async def ready() -> JSONResponse:
    """Readiness (Checkpoint I1 Phase 9B): can this process actually serve
    real traffic right now? Checks a real database query, the Alembic
    schema-currency status (`schema_upgrade_problems`, the same check
    `scripts/live_smoke.py` uses before any paid call), and provider
    CONFIGURATION only — `openai_api_key`/`tavily_api_key` being set, never
    an actual OpenAI/Tavily network call. 503 when the database is
    unreachable or the schema is behind head; 200 otherwise, providers or
    not (Demo Mode needs neither, so an unconfigured provider is reported
    but never makes the process "not ready")."""
    checks: dict[str, object] = {}
    healthy = True

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — readiness must never 500, only report unhealthy
        healthy = False
        checks["database"] = "unreachable"
        logger.warning("readiness check: database unreachable: %s", redact(str(exc)))

    if checks["database"] != "ok":
        checks["schema"] = "unknown"
    elif engine.dialect.name == "sqlite":
        # SQLite's local-dev schema is managed by create_all() (Phase 5),
        # never Alembic — `schema_upgrade_problems` would otherwise flag
        # every normal local SQLite file as "predates Alembic" and fail
        # readiness permanently for the one dialect that's never migrated
        # this way. Not tracked, not a readiness failure.
        checks["schema"] = "not_tracked (sqlite, managed by create_all)"
    else:
        try:
            problems = await schema_upgrade_problems(engine)
            checks["schema"] = "ok" if not problems else "behind"
            if problems:
                healthy = False
        except Exception as exc:  # noqa: BLE001
            healthy = False
            checks["schema"] = "unknown"
            logger.warning("readiness check: schema status unknown: %s", redact(str(exc)))

    # Configuration only — never a live provider call. An unconfigured
    # provider is informational, not a readiness failure: Demo Mode is
    # fully functional with neither key set.
    checks["providers"] = {
        "openai_configured": bool(settings.openai_api_key),
        "tavily_configured": bool(settings.tavily_api_key),
    }

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "not_ready", "checks": checks},
    )
