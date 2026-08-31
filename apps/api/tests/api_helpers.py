"""Small shared helpers for the API test suite — not fixtures, just plain
async functions any test file can import."""

from __future__ import annotations

import asyncio
import time
from typing import Any

DEMO_ICP_OVERRIDES = {
    "target_industries": ["ai_infrastructure"],
    "excluded_industries": ["retail_pos"],
    "adjacent_industries": {"data_tooling": ["ai_infrastructure"]},
    "size_band_min": 50,
    "size_band_max": 250,
    "target_funding_stages": ["series_a", "series_b"],
    "target_technologies": ["kubernetes", "pytorch", "triton"],
    "persona_titles": ["VP of Sales", "Head of Sales", "VP of Revenue"],
    "min_score": 60,
    "min_confidence": 0.6,
}


async def create_play(client, *, target_count: int = 7, icp_overrides: dict[str, Any] | None = None) -> dict:
    r = await client.post(
        "/api/plays",
        json={
            "objective": "Find AI infrastructure startups that recently raised funding.",
            "icp_overrides": DEMO_ICP_OVERRIDES if icp_overrides is None else icp_overrides,
            "target_count": target_count,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def start_run(client, play_id: str, *, seed: int = 42) -> dict:
    r = await client.post(f"/api/plays/{play_id}/runs", json={"seed": seed})
    assert r.status_code == 202, r.text
    return r.json()


async def wait_for_terminal(client, run_id: str, *, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = await client.get(f"/api/runs/{run_id}")
        assert r.status_code == 200, r.text
        data = r.json()
        if data["status"] != "RUNNING":
            return data
        await asyncio.sleep(0.1)
    raise TimeoutError(f"run {run_id} did not reach a terminal state within {timeout}s")
