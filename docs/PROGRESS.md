# Groundwork — Progress

Living state. Read this before touching anything — it tells you what's done, what's next, and what
not to re-litigate. Updated and committed at every checkpoint boundary (see
`docs/IMPLEMENTATION_PLAN.md` §30 for the checkpoint protocol).

---

## Completed checkpoints

| Checkpoint | Commit | Summary |
|---|---|---|
| **A — Foundation** | `6fafaa2414b2f3b75f8d0e9f2c36fe4003da9d09` (local; not yet pushed — see Known Issues) | Repo scaffolding, project-memory docs, FastAPI + Next.js health-check loop, CORS. |

---

## Current checkpoint

**A — Foundation.** Status: **complete**, ready to stop per the checkpoint protocol.

Next session should start **Checkpoint B — Core engine** (see `docs/IMPLEMENTATION_PLAN.md` §30),
after reading `CLAUDE.md`, `docs/ARCHITECTURE.md`, and this file.

---

## Tests written and verified

| Test | File | Status |
|---|---|---|
| `test_health_ok` | `apps/api/tests/test_health.py` | ✅ passing (`uv run pytest`) |

No domain/engine tests yet — they start at Checkpoint B (`test_scoring.py`, `test_dedupe.py`,
`test_grounding.py`, `test_review.py`, `test_isolation.py`, `test_fixture_provenance.py`,
`test_run_integration.py` per plan §25).

**Manual verification performed at Checkpoint A:**
- `uv run pytest` — 1/1 passed.
- `uv run uvicorn groundwork.main:app` on `:8000`, `curl localhost:8000/api/health` →
  `{"status":"ok","mode":"demo","version":"0.1.0"}`.
- `pnpm dev` on `:3000`, verified via headless Chromium (Playwright) that the page renders the API
  health payload (status/mode/version) fetched client-side from `:8000`, with CORS working.
- `pnpm build` — production build succeeds (typecheck + lint clean).

---

## Known issues / deviations from plan

- **Checkpoint A commit is NOT pushed to GitHub yet — this is an access problem, not a code
  problem.** The commit (`6fafaa2414b2f3b75f8d0e9f2c36fe4003da9d09`, "Checkpoint A: foundation
  scaffold + project memory docs") exists in the local working tree only. Both push paths available
  to this session failed with the same root cause:
  - `git push -u origin claude/gtm-prototype-planning-dg6h1l` → `403`:
    *"Claude doesn't have GitHub access to RutikYerunkar/prospect-os for your organization. An org
    admin can install the Claude GitHub App at
    https://github.com/apps/claude/installations/select_target, or reconnect GitHub from claude.ai
    settings
    (https://claude.ai/customize/connectors?auth_start=github&auth_start_force=1) to re-link an
    existing installation."*
  - The GitHub MCP server's `create_branch` (an independent auth path) → `403 Resource not
    accessible by integration` — confirming the integration has read access (`get_me`,
    `list_branches` succeeded) but no write/push access to this repo at all.
  - **The remote repo currently has only `master` at the original empty-repo commit
    (`29bfaa629ab90e7bf61a37cd235958bbb4156628`)** — the
    `claude/gtm-prototype-planning-dg6h1l` branch does not exist on GitHub yet, only locally.
  - **Fix (needs the user or an org admin, not a retry):** install the Claude GitHub App for this
    org at the URL above, or reconnect GitHub under claude.ai Settings → Connectors. Once access is
    restored, `git push -u origin claude/gtm-prototype-planning-dg6h1l` from this working tree will
    publish everything as-is — no rework needed.
  - Nothing else about Checkpoint A is affected: all code, docs, and tests described below are
    complete, verified, and committed locally.
- Otherwise none. Checkpoint A scope matches `docs/IMPLEMENTATION_PLAN.md` §30 exactly: no domain
  logic, no models, no engine, no fixtures, no visual system — just the scaffold and the
  health-check loop.
- `create-next-app` (Next 16 / React 19 / Tailwind 4) generated its own `apps/web/CLAUDE.md` and
  `apps/web/AGENTS.md` — these are tool-managed files Next.js regenerates on `next dev` to point at
  its own breaking-changes docs. They are unrelated to this project's memory system (the root
  `CLAUDE.md`) and were left in place rather than deleted, since `next dev` recreates them anyway.
- `Makefile`'s `seed` and `demo-reset` targets are stubs that print "not implemented yet" and exit
  1 — intentional. The scripts they'll call (`scripts/seed.py`, `scripts/reset.py`) don't exist
  until Checkpoint B, and a Makefile target should never silently no-op.

---

## Next task

**Checkpoint B — Core engine** (budget 120m, hard stop T+2:25 from the start of implementation).
Objective: the entire product works headlessly via `python -m groundwork.scripts.run_demo`. See
`docs/IMPLEMENTATION_PLAN.md` §30 "Checkpoint B" for the exact file list, internal ordering, and
acceptance criteria (six-prospect run, exact status distribution, ≥1 retry recorded, `test_isolation.py`
and `test_fixture_provenance.py` green).

**Do NOT build in Checkpoint B:** HTTP routes, React, cancellation, a generic DAG engine (hard cap
`engine/` at ~400 LOC), an LLM reviewer, live providers.

---

## Do not touch (finished areas)

- `apps/api/groundwork/{config.py, db.py, main.py}` — settled shape for Checkpoint A's scope.
  `config.py` and `db.py` will grow (more settings, real tables) in Checkpoint B, but their existing
  fields/behavior shouldn't be removed without reason.
- `apps/web/app/page.tsx`, `apps/web/lib/api.ts` — the health-check page and fetch wrapper. `page.tsx`
  will be replaced by the New Play screen at Checkpoint D; `api.ts` will grow but its existing
  `apiGet`/`ApiError` shape should be extended, not rewritten.
- Root `.env.example`, `Makefile`, `.gitignore` — structurally settled; add to them at later
  checkpoints rather than restructuring.
