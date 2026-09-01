.PHONY: dev api web test seed demo-reset demo live-smoke search-spike search-smoke

# Runs the API (:8000) and the web app (:3000) together. Ctrl-C stops both.
dev:
	$(MAKE) -j2 api web

api:
	cd apps/api && uv run uvicorn groundwork.main:app --reload --port 8000

web:
	cd apps/web && pnpm dev

test:
	cd apps/api && uv run pytest

# Ensures the schema exists and the fixture pack is valid.
seed:
	cd apps/api && uv run python -m groundwork.scripts.seed

# Wipes the local SQLite DB and reseeds the schema from fixtures.
demo-reset:
	cd apps/api && uv run python -m groundwork.scripts.reset

# Runs the full Demo Mode engine headlessly — no FastAPI, no React.
demo:
	cd apps/api && uv run python -m groundwork.scripts.run_demo

# Runs ONE real prospect through the real OpenAI API. Costs real money.
# Requires OPENAI_API_KEY and explicit confirmation — see scripts/live_smoke.py.
live-smoke:
	cd apps/api && uv run python -m groundwork.scripts.live_smoke --i-understand-this-costs-money

# H1 Phase 18 fact-finding spike ONLY — verifies the real Tavily SDK ahead
# of a Checkpoint H2 adapter. Requires TAVILY_API_KEY, explicit confirmation,
# and the `tavily` package installed separately (not a project dependency —
# see scripts/search_spike.py). Never run automatically by `make test`/CI.
search-spike:
	cd apps/api && uv run python -m groundwork.scripts.search_spike --i-understand-this-makes-real-calls

# H2 real end-to-end smoke: REAL OpenAI + REAL Tavily, real discovered
# companies, real money. Requires OPENAI_API_KEY, TAVILY_API_KEY, and
# explicit confirmation — see scripts/search_smoke.py. Never run
# automatically by `make test`/CI.
search-smoke:
	cd apps/api && uv run python -m groundwork.scripts.search_smoke --i-understand-this-costs-money
