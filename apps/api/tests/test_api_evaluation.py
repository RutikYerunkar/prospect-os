"""GET /runs/{id}/evaluation — computed from persisted records, not constants."""

from __future__ import annotations

from tests.api_helpers import create_play, start_run, wait_for_terminal


async def test_evaluation_unknown_run_is_404(client) -> None:
    r = await client.get("/api/runs/does-not-exist/evaluation")
    assert r.status_code == 404


async def test_evaluation_is_computed_from_this_runs_own_records(client) -> None:
    play = await create_play(client)
    run = await start_run(client, play["id"])
    final = await wait_for_terminal(client, run["run_id"])

    r = await client.get(f"/api/runs/{run['run_id']}/evaluation")
    assert r.status_code == 200
    ev = r.json()

    # Volume reconciles with the run's own counters — same underlying rows.
    assert ev["volume"]["discovered"] == 7
    assert ev["volume"]["qualified"] == final["counters"].get("PASS", 0)
    assert ev["volume"]["needs_review"] == final["counters"].get("NEEDS_REVIEW", 0)
    assert ev["volume"]["rejected"] == final["counters"].get("REJECTED", 0)
    assert ev["volume"]["duplicated"] == final["counters"].get("DUPLICATE", 0)

    # Quality metrics are real fractions, not sentinel/hardcoded values.
    assert 0.0 <= ev["quality"]["evidence_coverage"] <= 1.0
    assert 0.0 <= ev["quality"]["grounded_claim_rate"] <= 1.0
    assert ev["quality"]["mean_icp_score"] is not None
    assert set(ev["quality"]["provenance_mix"].keys()) == {"DEMO_FIXTURE"}
    assert sum(ev["quality"]["provenance_mix"].values()) > 0

    # Reliability reflects the scripted retry/failure fixtures actually firing.
    assert ev["reliability"]["total_retries"] >= 1
    assert ev["reliability"]["step_status_counts"].get("FAILED", 0) >= 1
    assert ev["reliability"]["p50_step_duration_ms"] is not None
    assert ev["reliability"]["run_wall_clock_ms"] is not None

    # All seven guardrail checks are represented with real pass rates.
    guardrail_ids = {g["id"] for g in ev["guardrails"]}
    assert guardrail_ids == {
        "claim_grounding",
        "no_fabricated_contact",
        "cross_prospect_leak",
        "no_placeholders",
        "duplicate_account",
        "score_support",
        "confidence_floor",
    }
    for g in ev["guardrails"]:
        assert 0.0 <= g["pass_rate"] <= 1.0
        assert g["total"] > 0


async def test_evaluation_on_a_run_with_no_prospects_yet_has_no_fabricated_numbers(client) -> None:
    play = await create_play(client)
    run = await start_run(client, play["id"])

    # Query immediately — before the background task has necessarily made progress.
    r = await client.get(f"/api/runs/{run['run_id']}/evaluation")
    assert r.status_code == 200
    ev = r.json()
    # Whatever has or hasn't happened yet, nothing here is a fabricated
    # placeholder: an unavailable metric is null, not a fake number.
    assert isinstance(ev["volume"]["discovered"], int)
    if ev["volume"]["discovered"] == 0:
        assert ev["quality"]["evidence_coverage"] is None
        assert ev["quality"]["mean_icp_score"] is None
