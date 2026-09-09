.PHONY: dev api web test seed demo-reset demo live-smoke search-spike search-smoke enrichment-smoke hunter-smoke gmail-scope-probe db-upgrade db-downgrade db-current db-history docker-build prod-smoke

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

# V2-D real smoke: ONE real Apollo `people/match` call per --person (max 2),
# real money. Requires APOLLO_API_KEY, explicit confirmation, and at least
# one --person — see scripts/enrichment_smoke.py. Never run automatically by
# `make test`/CI. Usage:
#   make enrichment-smoke PERSON="Jane Doe:example.com:VP of Sales"
enrichment-smoke:
	cd apps/api && uv run python -m groundwork.scripts.enrichment_smoke --i-understand-this-costs-money --person "$(PERSON)"

# V2-DH real smoke: ONE real Hunter `email-finder` call for --person, real
# money. Requires HUNTER_API_KEY, explicit confirmation, and exactly one
# --person — see scripts/hunter_smoke.py. Never run automatically by `make
# test`/CI. Usage:
#   make hunter-smoke PERSON="Jane Doe:example.com:VP of Sales"
hunter-smoke:
	cd apps/api && uv run python -m groundwork.scripts.hunter_smoke --i-understand-this-costs-money --person "$(PERSON)"

# V2-G hard gate: manual, READ-ONLY probe of whether gmail.metadata actually
# permits the bounded SENT-reconciliation reads §3.3 depends on. Requires a
# Gmail account already connected via the real API, and explicit
# confirmation — see scripts/gmail_scope_probe.py. Never run automatically
# by `make test`/CI. Zero send/write calls of any kind.
gmail-scope-probe:
	cd apps/api && uv run python -m groundwork.scripts.gmail_scope_probe --i-understand-this-reads-a-real-mailbox

# Alembic migrations (Checkpoint I1 Phase 5) — explicit schema management
# against whatever DATABASE_URL is currently set. SQLite local dev normally
# doesn't need this (`make demo-reset`/`create_all()` cover it); Postgres
# (local container or production) is managed exclusively through these.
db-upgrade:
	cd apps/api && uv run alembic upgrade head

db-downgrade:
	cd apps/api && uv run alembic downgrade -1

db-current:
	cd apps/api && uv run alembic current

db-history:
	cd apps/api && uv run alembic history

# Checkpoint I1 Phase 10 — builds the API image locally. Never pushes
# anywhere; no registry/cloud target exists yet (Checkpoint I2).
docker-build:
	cd apps/api && docker build -t groundwork-api .

# Author-only, never run automatically (not by this target, `make test`, or
# CI). Demo Mode only — zero paid provider calls regardless of what BASE_URL
# points at. No real deployment exists yet (Checkpoint I2), so there is
# nothing to run this against today; usage:
#   make prod-smoke BASE_URL=https://api.example.com
prod-smoke:
	cd apps/api && uv run python -m groundwork.scripts.prod_smoke --base-url "$(BASE_URL)" --i-understand-this-targets-a-real-deployment
