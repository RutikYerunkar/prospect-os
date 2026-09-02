"""POST /plays, POST /plays/{id}/runs (async 202 -> eventual terminal state),
GET /plays, GET /plays/{id}, GET /runs/{id}."""

from __future__ import annotations

from tests.api_helpers import create_play, login_as_operator, start_run, wait_for_terminal


async def test_create_play_builds_play_spec_from_objective_and_overrides(client) -> None:
    play = await create_play(client)
    assert play["objective_text"] == "Find AI infrastructure startups that recently raised funding."
    assert play["icp_spec"]["target_industries"] == ["ai_infrastructure"]
    assert play["icp_spec"]["excluded_industries"] == ["retail_pos"]
    assert play["mode"] == "demo"
    assert play["runs"] == []


async def test_create_play_live_mode_requires_operator_session(client) -> None:
    """Checkpoint I1 Phase 8: `mode="live"` alone grants nothing."""
    r = await client.post("/api/plays", json={"objective": "test", "mode": "live"})
    assert r.status_code == 401


async def test_create_play_accepts_live_mode_without_live_objective_parser(client, monkeypatch) -> None:
    # Checkpoint G: Play creation itself never requires live credentials
    # unless the caller explicitly asks for the live objective parser.
    # Objective parsing stays deterministic here (parse_source reflects it).
    # Checkpoint I1 Phase 8: an operator session is required for ANY
    # mode="live" request, regardless of use_live_objective_parser.
    await login_as_operator(client, monkeypatch)
    r = await client.post("/api/plays", json={"objective": "test", "mode": "live"})
    assert r.status_code == 201
    assert r.json()["parse_source"] == "deterministic"


async def test_start_run_rejects_live_mode_without_configured_runtime(client, monkeypatch) -> None:
    # No OPENAI_API_KEY is configured in tests, so `app.state.live_runtime`
    # is None — Live Mode must 422 cleanly here, never fall back to Demo.
    await login_as_operator(client, monkeypatch)
    play = await create_play(client)
    r = await client.post(f"/api/plays/{play['id']}/runs", json={"mode": "live"})
    assert r.status_code == 422


async def test_start_run_live_mode_requires_operator_session(client) -> None:
    play = await create_play(client)  # demo play; run-create body overrides to live
    r = await client.post(f"/api/plays/{play['id']}/runs", json={"mode": "live"})
    assert r.status_code == 401


async def test_get_play_and_list_plays(client) -> None:
    play = await create_play(client)

    r = await client.get(f"/api/plays/{play['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == play["id"]

    r = await client.get("/api/plays")
    assert r.status_code == 200
    assert any(p["id"] == play["id"] for p in r.json())


async def test_get_unknown_play_is_404(client) -> None:
    r = await client.get("/api/plays/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["status"] == 404
    assert "title" in body and "detail" in body


async def test_start_run_returns_202_immediately_without_waiting_for_completion(client) -> None:
    play = await create_play(client)

    run = await start_run(client, play["id"])
    assert run["status"] == "RUNNING"

    # The response came back before any prospect could possibly have finished
    # a 7-company fan-out — this is the "does not wait" contract, not just a
    # status-code check.
    r = await client.get(f"/api/runs/{run['run_id']}")
    assert r.status_code == 200
    assert r.json()["status"] in ("RUNNING", "PARTIAL", "COMPLETED")


async def test_run_reaches_a_terminal_state_and_counters_reconcile(client) -> None:
    play = await create_play(client)
    run = await start_run(client, play["id"])

    final = await wait_for_terminal(client, run["run_id"])
    assert final["status"] == "PARTIAL"  # Quarry Systems' scripted failure exhausts retries
    assert sum(final["counters"].values()) == 7
    assert final["finished_at"] is not None
    assert final["duration_ms"] is not None and final["duration_ms"] >= 0


async def test_start_run_on_unknown_play_is_404(client) -> None:
    r = await client.post("/api/plays/does-not-exist/runs", json={})
    assert r.status_code == 404


async def test_get_unknown_run_is_404(client) -> None:
    r = await client.get("/api/runs/does-not-exist")
    assert r.status_code == 404
