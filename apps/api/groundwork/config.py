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

    # Live-mode provider keys (P1). Never logged, never returned by any endpoint —
    # GET /settings/providers reports configured: bool only.
    openai_api_key: str | None = None
    tavily_api_key: str | None = None


settings = Settings()
