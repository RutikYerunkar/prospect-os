"""Small shared helpers for the API test suite — not fixtures, just plain
async functions any test file can import."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from groundwork.config import settings

TEST_OPERATOR_PASSPHRASE = "test-operator-passphrase"
TEST_SESSION_SIGNING_KEY = "test-session-signing-key-not-a-real-secret"


async def login_as_operator(client, monkeypatch, *, passphrase: str = TEST_OPERATOR_PASSPHRASE) -> None:
    """Configures operator login (unset by default in tests, matching a
    fresh deployment with no OPERATOR_PASSPHRASE) and logs the given
    `client` in — the resulting cookie rides along on every subsequent
    request `client` makes, exactly like a real browser session. Callers
    that need an UNauthenticated `client` after this should use a fresh
    `httpx.AsyncClient`/the `unauthenticated_client` fixture instead of
    trying to "log out" this one mid-test."""
    monkeypatch.setattr(settings, "operator_passphrase", passphrase)
    monkeypatch.setattr(settings, "session_signing_key", TEST_SESSION_SIGNING_KEY)
    # `client`'s default `Origin` header (see conftest.py) satisfies Phase
    # 8's CSRF guard here.
    r = await client.post("/api/operator/session", json={"passphrase": passphrase})
    assert r.status_code == 200, r.text

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
