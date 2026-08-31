.PHONY: dev api web test seed demo-reset demo

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
