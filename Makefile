.PHONY: dev api web test seed demo-reset

# Runs the API (:8000) and the web app (:3000) together. Ctrl-C stops both.
dev:
	$(MAKE) -j2 api web

api:
	cd apps/api && uv run uvicorn groundwork.main:app --reload --port 8000

web:
	cd apps/web && pnpm dev

test:
	cd apps/api && uv run pytest

# Populates the demo fixture pack. Lands in Checkpoint B.
seed:
	@echo "make seed is not implemented yet — it lands in Checkpoint B (fixtures + seed script)."
	@exit 1

# Wipes the local SQLite DB and reseeds from fixtures. Lands in Checkpoint B.
demo-reset:
	@echo "make demo-reset is not implemented yet — it lands in Checkpoint B."
	@exit 1
