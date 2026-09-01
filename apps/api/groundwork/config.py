from typing import Literal

from pydantic import field_validator
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

    # --- H1 Phase 14: hard search bounds for H2's live search adapter ---
    # Defined now, not exercised against a real vendor in H1 — no live
    # search happens in this checkpoint at all. `domain/query_plan.py` and
    # `domain/discovery.py` are offline-tested against these bounds; H2
    # wires an actual `TavilySearchProvider` that must respect them.
    live_max_plan_queries_per_run: int = 4
    live_max_domain_resolution_queries_per_run: int = 8
    live_max_source_queries_per_prospect: int = 3
    live_max_search_calls_per_run: int = 32
    search_max_transport_retries: int = 1
    live_max_result_occurrences_per_prospect: int = 15
    live_max_sources_per_prospect: int = 5
    live_max_extract_calls_per_run: int = 25
    live_max_search_results_per_query: int = 10

    @field_validator(
        "openai_price_input_usd_per_mtok", "openai_price_output_usd_per_mtok", "live_run_soft_budget_usd",
        mode="before",
    )
    @classmethod
    def _blank_optional_float_is_none(cls, value: object) -> object:
        """`.env.example` documents these as optional and blank by default
        (`OPENAI_PRICE_INPUT_USD_PER_MTOK=`) — copying it to `.env` verbatim
        must never crash Settings construction. Pydantic's own float parsing
        rejects `""` outright; this normalizes a blank/whitespace-only
        string to `None` *before* type coercion runs, preserving the
        documented "unset -> None -> cost stays null / threshold
        unenforceable" semantics rather than requiring the user to notice
        and manually delete the line.
        """
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


settings = Settings()
