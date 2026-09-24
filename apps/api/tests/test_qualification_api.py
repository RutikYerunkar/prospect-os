"""v2.0.3 — `PlaySpec.min_score` server validation reaches the API as the
existing 422 path (`api/routers/plays.py` already catches `ValidationError`
from `parse_objective()`/`PlaySpec.model_validate` and re-raises
`UnprocessableEntityError`) — no new error-handling code, just a newly
enforced `Field(ge=0, le=100)`.
"""

from __future__ import annotations

import pytest

from tests.api_helpers import DEMO_ICP_OVERRIDES


@pytest.mark.parametrize("min_score", [-1, 101, -50, 1000])
async def test_create_play_rejects_out_of_range_min_score(client, min_score: int) -> None:
    overrides = {**DEMO_ICP_OVERRIDES, "min_score": min_score}
    r = await client.post(
        "/api/plays",
        json={"objective": "Find AI infrastructure startups.", "icp_overrides": overrides, "target_count": 7},
    )
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("min_score", [0, 60, 100])
async def test_create_play_accepts_boundary_min_score(client, min_score: int) -> None:
    overrides = {**DEMO_ICP_OVERRIDES, "min_score": min_score}
    r = await client.post(
        "/api/plays",
        json={"objective": "Find AI infrastructure startups.", "icp_overrides": overrides, "target_count": 7},
    )
    assert r.status_code == 201, r.text
    assert r.json()["icp_spec"]["min_score"] == min_score


@pytest.mark.parametrize("min_score", [-1, 101])
async def test_preview_play_rejects_out_of_range_min_score(client, min_score: int) -> None:
    overrides = {**DEMO_ICP_OVERRIDES, "min_score": min_score}
    r = await client.post(
        "/api/plays/preview",
        json={"objective": "Find AI infrastructure startups.", "icp_overrides": overrides, "target_count": 7},
    )
    assert r.status_code == 422, r.text
