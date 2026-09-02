"""Checkpoint I1 Phase 7 — `POST /api/plays/preview`: non-persisting,
deterministic, never an LLM call.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from groundwork.models.tables import LLMCallRow, PlayRow
from tests.api_helpers import DEMO_ICP_OVERRIDES

_OBJECTIVE = "Find AI infrastructure startups that recently raised funding."


async def _count(session_factory, model) -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(model))
        return result.scalar_one()


async def test_preview_returns_a_playspec_without_persisting_anything(client, session_factory):
    r = await client.post(
        "/api/plays/preview",
        json={"objective": _OBJECTIVE, "icp_overrides": DEMO_ICP_OVERRIDES, "target_count": 7},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parse_source"] == "deterministic"
    assert body["icp_spec"]["objective_text"] == _OBJECTIVE
    assert body["icp_spec"]["target_count"] == 7
    assert body["icp_spec"]["target_industries"] == DEMO_ICP_OVERRIDES["target_industries"]

    assert await _count(session_factory, PlayRow) == 0
    assert await _count(session_factory, LLMCallRow) == 0


async def test_100_preview_requests_create_zero_play_and_zero_llm_calls_rows(client, session_factory):
    responses = await asyncio.gather(
        *[
            client.post(
                "/api/plays/preview",
                json={"objective": _OBJECTIVE, "icp_overrides": DEMO_ICP_OVERRIDES, "target_count": 7},
            )
            for _ in range(100)
        ]
    )
    assert all(r.status_code == 200 for r in responses)

    assert await _count(session_factory, PlayRow) == 0
    assert await _count(session_factory, LLMCallRow) == 0


async def test_preview_use_live_objective_parser_field_is_rejected(client):
    r = await client.post(
        "/api/plays/preview",
        json={
            "objective": _OBJECTIVE,
            "icp_overrides": {},
            "target_count": 7,
            "use_live_objective_parser": True,
        },
    )
    assert r.status_code == 422, r.text


async def test_preview_mode_field_is_rejected(client):
    """`mode` isn't accepted either — preview is unconditionally
    deterministic regardless of Demo/Live, so there's nothing for a mode
    field to select between here."""
    r = await client.post(
        "/api/plays/preview",
        json={"objective": _OBJECTIVE, "icp_overrides": {}, "target_count": 7, "mode": "live"},
    )
    assert r.status_code == 422, r.text


async def test_preview_and_committed_deterministic_specs_are_equal_for_identical_input(client, session_factory):
    preview_r = await client.post(
        "/api/plays/preview",
        json={"objective": _OBJECTIVE, "icp_overrides": DEMO_ICP_OVERRIDES, "target_count": 7},
    )
    assert preview_r.status_code == 200, preview_r.text
    preview_spec = preview_r.json()["icp_spec"]

    committed_r = await client.post(
        "/api/plays",
        json={"objective": _OBJECTIVE, "icp_overrides": DEMO_ICP_OVERRIDES, "target_count": 7, "mode": "demo"},
    )
    assert committed_r.status_code == 201, committed_r.text
    committed_body = committed_r.json()
    assert committed_body["parse_source"] == "deterministic"  # Demo Mode never calls the LLM here either
    # `_parse_source` is a persisted implementation detail `create_play`
    # stashes inside the stored `icp_spec` JSON blob (see
    # `api/routers/plays.py::create_play`) — not part of the `PlaySpec`
    # itself, and preview (which persists nothing) has no equivalent to
    # compare against.
    committed_spec = {k: v for k, v in committed_body["icp_spec"].items() if k != "_parse_source"}

    assert preview_spec == committed_spec
    # Exactly the one committed Play exists — the preview call(s) above
    # created none.
    assert await _count(session_factory, PlayRow) == 1


async def test_preview_invalid_icp_overrides_is_422_not_500(client):
    r = await client.post(
        "/api/plays/preview",
        json={"objective": _OBJECTIVE, "icp_overrides": {"size_band_min": "not-a-number"}, "target_count": 7},
    )
    assert r.status_code == 422, r.text


async def test_run_agents_creates_exactly_one_play_and_one_run(client, session_factory):
    """The full "type -> preview repeatedly -> Run Agents" flow: any number
    of preview calls, then exactly one committed Play and one Run."""
    for _ in range(5):
        r = await client.post(
            "/api/plays/preview",
            json={"objective": _OBJECTIVE, "icp_overrides": DEMO_ICP_OVERRIDES, "target_count": 7},
        )
        assert r.status_code == 200

    play_r = await client.post(
        "/api/plays",
        json={"objective": _OBJECTIVE, "icp_overrides": DEMO_ICP_OVERRIDES, "target_count": 7, "mode": "demo"},
    )
    assert play_r.status_code == 201
    play_id = play_r.json()["id"]

    run_r = await client.post(f"/api/plays/{play_id}/runs", json={"seed": 42})
    assert run_r.status_code == 202

    assert await _count(session_factory, PlayRow) == 1

    from groundwork.models.tables import RunRow

    assert await _count(session_factory, RunRow) == 1
