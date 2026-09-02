"""`python -m groundwork.scripts.prod_smoke --base-url <url> --i-understand-this-targets-a-real-deployment`

Checkpoint I1's author-only prod smoke script. **Never executed by this session, `make test`, or CI —
no real deployment exists yet (Checkpoint I2 is not started).** It exists so a future session has
something ready to run the moment I2 provisions a real target, instead of writing this under time
pressure against a live system.

Zero-paid by construction: this script only ever drives **Demo Mode**. It has no flag, argument, or
code path that can request Live Mode — verifying a real deployment's health/readiness and its Demo Mode
path never risks a real OpenAI/Tavily charge, regardless of what's configured on the target.

Three checks, in order, each printed clearly and each a hard stop on failure:

1. `GET /api/health` — process liveness. A non-200 here means don't bother with anything else.
2. `GET /api/ready` — real readiness (DB reachable, Postgres schema current). Reports the full `checks`
   object; a `503` fails loudly with the specific reason, never a bare "not ready".
3. **One real Demo Mode run**, through the actual HTTP API exactly like a browser would: create a play,
   start a run, poll until terminal, and assert it reached a real terminal status with at least one
   completed prospect. This is the same "does the whole stack actually work" check
   `groundwork/scripts/run_demo.py` does headlessly against the engine directly — this one instead
   proves the deployed HTTP surface, SSE-adjacent polling, and the database round-trip all work against
   whatever's actually running at `--base-url`.

Requires the exact `--i-understand-this-targets-a-real-deployment` flag and an explicit `--base-url`
(no default — never silently targets `localhost` and calls that a "prod" smoke). Exits nonzero on any
failure, with the specific reason printed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

_POLL_INTERVAL_S = 1.0
_POLL_TIMEOUT_S = 60.0
# RunStatus's actual terminal values (see groundwork/models/enums.py) — RUNNING is the only
# non-terminal one.
_TERMINAL_RUN_STATUSES = {"COMPLETED", "PARTIAL", "INTERRUPTED"}
# ProspectStatus's non-terminal values — everything else (PASS/NEEDS_REVIEW/REJECTED/DUPLICATE/
# FAILED/TIMED_OUT) is terminal.
_NON_TERMINAL_PROSPECT_STATUSES = {"PENDING", "RUNNING"}

# The exact canonical Demo Mode ICP overrides `run_demo.py`/the fixture pack's own `play_spec` use —
# reusing them here means a real deployment's terminal counters are checkable against the same
# well-understood reference distribution documented throughout this repo, not an ad hoc smoke play.
_DEMO_OBJECTIVE = "Find fast-growing B2B SaaS companies in AI infrastructure ready to buy a GTM platform."
_DEMO_ICP_OVERRIDES = {
    "target_industries": ["ai_infrastructure"],
    "size_band_min": 50,
    "size_band_max": 250,
    "min_score": 60,
}


async def _check_health(client: httpx.AsyncClient) -> None:
    print("--- GET /api/health ---")
    resp = await client.get("/api/health")
    print(f"status={resp.status_code} body={resp.text}")
    if resp.status_code != 200:
        raise SystemExit(f"FAILURE: /api/health returned {resp.status_code}, expected 200")


async def _check_ready(client: httpx.AsyncClient) -> None:
    print("\n--- GET /api/ready ---")
    resp = await client.get("/api/ready")
    print(f"status={resp.status_code} body={resp.text}")
    if resp.status_code != 200:
        raise SystemExit(f"FAILURE: /api/ready returned {resp.status_code} — target is not ready to serve traffic")


async def _run_demo_smoke(client: httpx.AsyncClient) -> None:
    print("\n--- real Demo Mode run through the HTTP API ---")
    create_resp = await client.post(
        "/api/plays",
        json={
            "objective": _DEMO_OBJECTIVE,
            "icp_overrides": _DEMO_ICP_OVERRIDES,
            "mode": "demo",
        },
    )
    if create_resp.status_code != 201:
        raise SystemExit(f"FAILURE: POST /api/plays returned {create_resp.status_code}: {create_resp.text}")
    play = create_resp.json()
    play_id = play["id"]
    print(f"created play {play_id}")

    run_resp = await client.post(f"/api/plays/{play_id}/runs", json={})
    if run_resp.status_code != 202:
        raise SystemExit(f"FAILURE: POST /api/plays/{play_id}/runs returned {run_resp.status_code}: {run_resp.text}")
    run_id = run_resp.json()["run_id"]
    print(f"started run {run_id}")

    elapsed = 0.0
    status = None
    while elapsed < _POLL_TIMEOUT_S:
        poll_resp = await client.get(f"/api/runs/{run_id}")
        poll_resp.raise_for_status()
        run = poll_resp.json()
        status = run["status"]
        if status in _TERMINAL_RUN_STATUSES:
            break
        await asyncio.sleep(_POLL_INTERVAL_S)
        elapsed += _POLL_INTERVAL_S
    else:
        raise SystemExit(f"FAILURE: run {run_id} did not reach a terminal status within {_POLL_TIMEOUT_S}s")

    print(f"run reached terminal status: {status}")

    prospects_resp = await client.get(f"/api/runs/{run_id}/prospects")
    prospects_resp.raise_for_status()
    prospects = prospects_resp.json()
    completed = [p for p in prospects if p["status"] not in _NON_TERMINAL_PROSPECT_STATUSES]
    print(f"prospects: {len(prospects)} total, {len(completed)} reached a terminal status")
    if not completed:
        raise SystemExit("FAILURE: run reached a terminal status but zero prospects did — real bug, not a smoke artifact")


async def main(base_url: str) -> int:
    print(f"=== Groundwork prod smoke — target: {base_url} ===")
    print("Demo Mode only — this script cannot trigger Live Mode and makes zero paid provider calls.\n")
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        await _check_health(client)
        await _check_ready(client)
        await _run_demo_smoke(client)
    print("\nAll checks passed.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="the deployment's base URL, e.g. https://api.example.com")
    parser.add_argument(
        "--i-understand-this-targets-a-real-deployment", action="store_true", dest="confirmed",
        help="required — this script makes real HTTP requests, including a real (Demo Mode, zero-paid) run, "
        "against whatever --base-url points at",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.confirmed:
        print("Refusing to run without --i-understand-this-targets-a-real-deployment.", file=sys.stderr)
        sys.exit(1)
    sys.exit(asyncio.run(main(args.base_url)))
