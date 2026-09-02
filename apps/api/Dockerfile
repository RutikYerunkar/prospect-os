# Checkpoint I1 Phase 10 — packaging only. This builds the API image; it is
# never invoked by CI to push anywhere, and no cloud target exists yet (that
# is Checkpoint I2). See docs/DEPLOYMENT.md for what a real deployment needs
# on top of this image (DATABASE_URL pointed at managed Postgres, `alembic
# upgrade head` run once before the process starts, secrets injected as
# platform environment variables — never baked in here).
FROM python:3.11-slim AS base

# uv installs deterministically from the committed lockfile (`uv sync
# --frozen`) — never a floating `pip install -r requirements.txt` resolve.
# Installed from PyPI (pinned) rather than copied from the astral-sh/uv
# image, so this build has exactly one upstream registry dependency.
RUN pip install --no-cache-dir uv==0.5.11

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Dependencies first — cached across builds that only touch application code.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY groundwork ./groundwork
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
RUN uv sync --frozen --no-dev

# Non-root — the image never runs as root, in dev or otherwise.
RUN useradd --create-home --shell /usr/sbin/nologin groundwork
USER groundwork

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# No --reload (dev-only), no baked-in secrets (DATABASE_URL/OPENAI_API_KEY/
# etc. are supplied by the runtime environment, never COPYd or ARG'd into
# this image). $PORT falls back to 8000 for a plain `docker run`.
# workers=1 — this prototype is explicitly single-instance/single-worker
# (see docs/ARCHITECTURE.md — the in-process rate limiter and execution
# lease are both correct for exactly one running process, not more).
CMD ["sh", "-c", "exec uvicorn groundwork.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
