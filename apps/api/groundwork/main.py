from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from groundwork.api.errors import register_error_handlers
from groundwork.api.routers import evaluation, plays, prospects, runs, settings as settings_router
from groundwork.config import settings
from groundwork.db import SessionLocal, create_all, engine
from groundwork.repositories.runs import RunRepository


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all()
    # Honest crash recovery (§17): any run left RUNNING from a prior process
    # is marked INTERRUPTED rather than silently left looking active.
    await RunRepository(SessionLocal).sweep_interrupted()

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

    if app.state.live_runtime is not None:
        await app.state.live_runtime.close()
    if app.state.live_search_runtime is not None:
        await app.state.live_search_runtime.close()
    await engine.dispose()


app = FastAPI(title="Groundwork API", version=settings.app_version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(plays.router)
app.include_router(runs.router)
app.include_router(evaluation.router)
app.include_router(prospects.router)
app.include_router(settings_router.router)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mode": settings.mode,
        "version": settings.app_version,
    }
