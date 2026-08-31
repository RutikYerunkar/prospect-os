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
    yield
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
