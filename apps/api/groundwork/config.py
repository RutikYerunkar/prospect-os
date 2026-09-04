import json
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration. All values come from the environment — see .env.example.

    Deliberately minimal at Checkpoint A: MODE and concurrency are named here
    because §22a and the repo layout call for it, but the domain/engine/provider
    code that reads them doesn't exist until Checkpoint B.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_version: str = "0.1.0"
    mode: Literal["demo", "live"] = "demo"

    # --- Checkpoint I1: environment/process identity ---
    # Never branches engine/domain behavior (per CLAUDE.md's "never
    # special-case if demo mode" invariant, extended here to environment) —
    # it only governs process-level concerns: cookie Secure flag, opaque vs.
    # detailed error responses, log formatting.
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite+aiosqlite:///./groundwork.db"
    # Small pool appropriate for ONE API instance, ONE uvicorn worker — see
    # docs/DEPLOYMENT.md. Never sized for horizontal scaling that doesn't
    # exist yet. Ignored entirely for the SQLite dialect (NullPool-equivalent
    # single-file access; see `db.py`).
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_pre_ping: bool = True

    max_concurrent_prospects: int = 3
    run_wall_clock_timeout_s: float = 180.0

    # --- v2 §Part 4/§E: contact-enrichment call budget ---
    # A per-run HARD ceiling on enrichment-provider calls (`EnrichmentCallBudget`,
    # checked inside the provider itself, exactly like `SearchCallBudget`).
    # Generous relative to `target_count` (7 in the canonical demo) — this
    # exists as a structural safety bound, not a throttle a normal run should
    # ever hit.
    max_enrichment_calls_per_run: int = 20

    # --- V2-D/V2-DH: Live Apollo/Hunter contact enrichment ---
    # Selects which `EnrichmentProvider` slot Live Mode wires, independent of
    # `mode`/`openai_api_key`/`tavily_api_key` — enrichment is optional even
    # in Live Mode. "none" -> `enrichment=None` -> NOT_ATTEMPTED, zero
    # provider calls, never a fixture fallback. Never special-cased inside
    # `engine/`/`domain/` — only `providers/registry.py`'s Live wiring reads
    # this.
    enrichment_provider: Literal["none", "apollo", "hunter"] = "none"
    # Never logged, never persisted, never returned by any endpoint (added to
    # `observability/redact.py`'s choke point) — GET /settings/providers
    # reports `configured: bool` only.
    apollo_api_key: str | None = None
    apollo_call_deadline_s: float = 15.0
    apollo_max_concurrency: int = 2
    apollo_max_transport_retries: int = 1
    # Unset -> `cost_usd` stays null for every enrichment_calls row (mirrors
    # `tavily_price_usd_per_credit`) — as of V2-D this is moot regardless,
    # since no verified numeric Apollo usage field has ever been observed
    # (see `ApolloRuntime.estimate_cost_usd`'s docstring), so `credits_used`
    # is never populated for this rate to even apply to.
    apollo_price_usd_per_credit: float | None = None
    # No `APOLLO_BASE_URL` — the endpoint/origin/path are pinned constants in
    # `providers/live/enrichment_runtime.py`, deliberately not configurable.

    # V2-DH: Hunter is a SECOND Live `EnrichmentProvider`, behind the same
    # Protocol Apollo satisfies — never a second pipeline. Never logged,
    # never persisted, never returned by any endpoint.
    hunter_api_key: str | None = None
    hunter_call_deadline_s: float = 15.0
    hunter_max_concurrency: int = 2
    hunter_max_transport_retries: int = 1
    # Deliberately NO `hunter_price_usd_per_credit` field (frozen §Part 12)
    # — `credits_used`/`cost_usd` stay permanently `None` for every Hunter
    # attempt. No `HUNTER_BASE_URL` — pinned constants in
    # `providers/live/hunter_runtime.py`.

    # `NoDecode`: pydantic-settings would otherwise try to JSON-decode any
    # env value for a `list[str]` field *before* our own validator runs, and
    # raise a hard `SettingsError` on a plain comma-separated string (invalid
    # JSON) rather than ever handing it to `_parse_cors_origins` below.
    # `NoDecode` disables that pre-parse so the raw string always reaches the
    # validator, which then handles both forms itself.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        """Accepts a JSON list (`CORS_ORIGINS=["http://a","http://b"]`) *or*
        a plain comma-separated string (`CORS_ORIGINS=http://a,http://b`) —
        pydantic-settings' default env parsing for `list[str]` only accepts
        the former and raises on the latter, which is the more natural form
        to hand-type into a host's environment-variable UI. Runs `mode="before"`
        so it sees the raw env string before pydantic's own list coercion."""
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            return json.loads(stripped)
        return [origin.strip() for origin in stripped.split(",") if origin.strip()]

    # --- Checkpoint I1: operator session (Phase 8) ---
    # Live Mode is hard-disabled whenever `operator_passphrase` is unset —
    # there is no other way to obtain a session. Never logged, never
    # returned by any endpoint.
    operator_passphrase: str | None = None
    # Signs/verifies the operator session cookie (itsdangerous
    # `URLSafeTimedSerializer`). Required for the operator-session endpoints
    # to function; unset means Live stays locked even if a passphrase is set
    # (fail closed, not an unsigned cookie).
    session_signing_key: str | None = None
    # Optional second key accepted for *verification only*, to allow one
    # rotation without invalidating every existing session immediately —
    # new cookies are always signed with `session_signing_key`.
    session_signing_key_old: str | None = None
    session_max_age_s: int = 60 * 60 * 12  # 12h
    operator_login_rate_limit_attempts: int = 5
    operator_login_rate_limit_window_s: float = 60.0

    # --- Checkpoint I1: Live cost/abuse controls (Phase 8B) ---
    live_max_active_runs: int = 1
    live_daily_run_allowance: int = 10

    # In-process, per-client-IP rate limits on the public (unauthenticated)
    # write/preview surface — correct for ONE API instance, NOT a
    # distributed rate limit (see `api/rate_limit.py`). Generous enough not
    # to bother a legitimate user typing in the New Play form (preview
    # debounces at 600ms => ~100/min sustained) while still bounding
    # deliberate abuse.
    public_write_rate_limit_attempts: int = 30
    public_write_rate_limit_window_s: float = 60.0
    preview_rate_limit_attempts: int = 120
    preview_rate_limit_window_s: float = 60.0

    # Hard cap on request body size (Content-Length), enforced by
    # `MaxBodySizeMiddleware` in `main.py` before any route/Pydantic
    # validation runs. Generous relative to any real request this API
    # accepts (the largest legitimate body is a `PlayCreateRequest`/
    # `PlayPreviewRequest`, and `objective` alone is already capped at 2000
    # chars) — this exists to reject a deliberately oversized body outright,
    # not to constrain normal use.
    max_request_body_bytes: int = 256_000

    # --- Checkpoint I1 Phase 9: request/host/error hardening ---
    # `["*"]` (any host) preserves today's unrestricted behavior for local
    # dev/tests. A production deployment should set this explicitly (see
    # docs/DEPLOYMENT.md) to the API's real hostname(s) — TrustedHostMiddleware
    # rejects a request whose Host header doesn't match, a defense against
    # Host-header-based attacks (cache poisoning, password-reset-link
    # poisoning) that CORS/Origin checks don't cover (Host is a request
    # header the browser sets from the URL bar, not a CORS-governed one).
    trusted_hosts: Annotated[list[str], NoDecode] = ["*"]

    @field_validator("trusted_hosts", mode="before")
    @classmethod
    def _parse_trusted_hosts(cls, value: object) -> object:
        """Same comma-or-JSON-list parsing as `cors_origins` — see
        `_parse_cors_origins` above for why `NoDecode` is required too."""
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return ["*"]
        if stripped.startswith("["):
            return json.loads(stripped)
        return [host.strip() for host in stripped.split(",") if host.strip()]

    # --- Checkpoint I1: execution lease (Phase 4) ---
    executor_heartbeat_interval_s: float = 10.0
    # Reaper threshold — a RUNNING run whose heartbeat is older than this is
    # considered abandoned by a dead/killed process. Must be comfortably
    # larger than `executor_heartbeat_interval_s` so a single missed
    # heartbeat under normal GC/scheduling jitter never trips it.
    executor_stale_threshold_s: float = 60.0
    executor_reaper_interval_s: float = 30.0
    # Bounded drain window on graceful shutdown (SIGTERM) before in-flight
    # runs are force-transitioned to INTERRUPTED.
    shutdown_drain_timeout_s: float = 20.0

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
    # H2 post-smoke: DISCOVERY_EXTRACTION reads up to MAX_DISCOVERY_HITS
    # (40) real search-result excerpts in one call — a real first smoke
    # exhausted LLM_MAX_TRANSPORT_RETRIES against the shared 30s deadline
    # on exactly this operation (35 hits), producing zero candidates with
    # no distinguishing signal from a genuine "no companies found." This
    # is a dedicated, larger deadline for that one bulkier operation only
    # — every other Live LLM operation (research/score/personalize/
    # domain_selection) keeps the shared, already-verified 30s budget.
    llm_discovery_call_deadline_s: float = 60.0
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

    # --- H2: real Tavily adapter runtime bounds ---
    search_call_deadline_s: float = 20.0
    search_max_concurrency: int = 2
    # Bounded persisted excerpt length for LIVE_FETCH source content (Phase
    # 11) — raw HTML/full page bodies are never persisted, only a bounded
    # extracted excerpt.
    live_max_source_excerpt_chars: int = 1200
    tavily_search_depth: str = "basic"
    # Tavily pricing is NOT baked into code unless verified/configured —
    # unset -> cost_usd stays null everywhere for search telemetry, exactly
    # like the OpenAI pricing fields above. There is no publicly documented,
    # stable per-credit USD rate to hardcode, so this defaults unset.
    tavily_price_usd_per_credit: float | None = None

    @field_validator(
        "openai_price_input_usd_per_mtok", "openai_price_output_usd_per_mtok", "live_run_soft_budget_usd",
        "tavily_price_usd_per_credit", "apollo_price_usd_per_credit",
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
