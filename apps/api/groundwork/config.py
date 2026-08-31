from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration. All values come from the environment — see .env.example.

    Deliberately minimal at Checkpoint A: MODE and concurrency are named here
    because §22a and the repo layout call for it, but the domain/engine/provider
    code that reads them doesn't exist until Checkpoint B.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_version: str = "0.1.0"
    mode: Literal["demo", "live"] = "demo"

    database_url: str = "sqlite+aiosqlite:///./groundwork.db"

    max_concurrent_prospects: int = 3
    run_wall_clock_timeout_s: float = 180.0

    cors_origins: list[str] = ["http://localhost:3000"]

    # Live-mode provider keys (Checkpoint G). Never logged, never returned by
    # any endpoint — GET /settings/providers reports configured: bool only.
    openai_api_key: str | None = None
    tavily_api_key: str | None = None

    # --- Live Mode cost/safety bounds (Checkpoint G §7) ---
    # Model selection is config-only — no application code branches on this
    # string. gpt-5.6-luna is the named lower-cost alternative profile.
    openai_model: str = "gpt-5.6-terra"
    # "" (empty) omits the `reasoning` field from the Responses request
    # entirely, rather than sending an empty/invalid value.
    openai_reasoning_effort: str = "low"

    live_max_prospects_per_run: int = 5
    llm_max_concurrency: int = 2
    llm_max_transport_retries: int = 2
    llm_max_schema_retries: int = 1
    live_step_timeout_s: float = 45.0
    live_run_wall_clock_timeout_s: float = 600.0
    llm_call_deadline_s: float = 30.0
    # Measurement-selected (see docs/PROGRESS.md, Checkpoint G Phase 4): the
    # largest measured operation (research_extraction, worst-case padded
    # facts) serializes to ~3KB / ~800 visible tokens. 2048 leaves headroom
    # for low-effort reasoning tokens (which count toward this same budget)
    # plus real-model verbosity above the synthetic measurement, without
    # preserving the plan's provisional 3000 for its own sake.
    llm_max_output_tokens: int = 2048

    # Pricing is NOT baked into code unless verified (§7). Unset -> cost_usd
    # is always null; the UI states the monetary threshold isn't enforceable
    # rather than inventing a number.
    openai_price_input_usd_per_mtok: float | None = None
    openai_price_output_usd_per_mtok: float | None = None
    # Soft threshold only — never described as a cap/ceiling. None disables
    # budget enforcement entirely (no threshold configured).
    live_run_soft_budget_usd: float | None = None


settings = Settings()
