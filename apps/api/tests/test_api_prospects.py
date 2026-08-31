"""GET /runs/{id}/prospects, GET /prospects/{id}, approve/reject transitions."""

from __future__ import annotations

from tests.api_helpers import create_play, start_run, wait_for_terminal


async def _run_to_completion(client):
    play = await create_play(client)
    run = await start_run(client, play["id"])
    final = await wait_for_terminal(client, run["run_id"])
    return run["run_id"], final


async def test_prospect_summaries_expose_board_fields(client) -> None:
    run_id, _ = await _run_to_completion(client)

    r = await client.get(f"/api/runs/{run_id}/prospects")
    assert r.status_code == 200
    prospects = r.json()
    assert len(prospects) == 7

    by_status = {p["status"] for p in prospects}
    assert {"PASS", "NEEDS_REVIEW", "REJECTED", "DUPLICATE", "FAILED"} <= by_status

    passed = next(p for p in prospects if p["status"] == "PASS")
    assert passed["icp_score"] is not None
    assert passed["confidence"] is not None
    assert passed["approval_state"] == "PENDING"

    duplicate = next(p for p in prospects if p["status"] == "DUPLICATE")
    assert duplicate["icp_score"] is None

    quarry = next(p for p in prospects if p["status"] == "FAILED")
    assert quarry["had_retry"] is True


async def test_prospect_summaries_for_unknown_run_is_404(client) -> None:
    r = await client.get("/api/runs/does-not-exist/prospects")
    assert r.status_code == 404


async def test_prospect_aggregate_has_full_provenance_chain(client) -> None:
    run_id, _ = await _run_to_completion(client)
    prospects = (await client.get(f"/api/runs/{run_id}/prospects")).json()
    passed = next(p for p in prospects if p["status"] == "PASS")

    r = await client.get(f"/api/prospects/{passed['id']}")
    assert r.status_code == 200
    agg = r.json()

    assert agg["company"]["display_name"]
    assert agg["score"]["overall"] == passed["icp_score"]
    # score contributions sum to the displayed overall (within rounding)
    total_contribution = sum(d["contribution"] for d in agg["score"]["dimensions"])
    assert abs(round(100 * total_contribution) - agg["score"]["overall"]) <= 1
    assert agg["review"]["verdict"] == "PASS"
    assert all(check["passed"] for check in agg["review"]["checks"])
    assert len(agg["evidence"]) > 0
    assert len(agg["trace"]) > 0
    assert agg["approval"]["state"] == "PENDING"


async def test_prospect_aggregate_unknown_id_is_404(client) -> None:
    r = await client.get("/api/prospects/does-not-exist")
    assert r.status_code == 404


async def test_approve_is_a_pure_state_transition(client) -> None:
    run_id, _ = await _run_to_completion(client)
    prospects = (await client.get(f"/api/runs/{run_id}/prospects")).json()
    passed = next(p for p in prospects if p["status"] == "PASS")

    r = await client.post(f"/api/prospects/{passed['id']}/approve", json={"actor": "alice"})
    assert r.status_code == 200
    agg = r.json()
    assert agg["approval"] == {
        "state": "APPROVED",
        "actor": "alice",
        "reason": None,
        "decided_at": agg["approval"]["decided_at"],
    }
    # the engine-computed status is untouched by the human decision
    assert agg["status"] == "PASS"

    # reflected on the run-level summary too
    r = await client.get(f"/api/runs/{run_id}/prospects")
    updated = next(p for p in r.json() if p["id"] == passed["id"])
    assert updated["approval_state"] == "APPROVED"


async def test_reject_requires_a_reason_and_records_it(client) -> None:
    run_id, _ = await _run_to_completion(client)
    prospects = (await client.get(f"/api/runs/{run_id}/prospects")).json()
    needs_review = next(p for p in prospects if p["status"] == "NEEDS_REVIEW")

    r = await client.post(f"/api/prospects/{needs_review['id']}/reject", json={"reason": "not a fit"})
    assert r.status_code == 200
    agg = r.json()
    assert agg["approval"]["state"] == "REJECTED"
    assert agg["approval"]["reason"] == "not a fit"


async def test_cannot_decide_a_duplicate_or_failed_prospect(client) -> None:
    run_id, _ = await _run_to_completion(client)
    prospects = (await client.get(f"/api/runs/{run_id}/prospects")).json()
    duplicate = next(p for p in prospects if p["status"] == "DUPLICATE")

    r = await client.post(f"/api/prospects/{duplicate['id']}/approve", json={})
    assert r.status_code == 409
    body = r.json()
    assert body["status"] == 409

    failed = next(p for p in prospects if p["status"] == "FAILED")
    r = await client.post(f"/api/prospects/{failed['id']}/reject", json={"reason": "n/a"})
    assert r.status_code == 409


async def test_decide_unknown_prospect_is_404(client) -> None:
    r = await client.post("/api/prospects/does-not-exist/approve", json={})
    assert r.status_code == 404
