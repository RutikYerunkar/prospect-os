"""Checkpoint G Phase 3: Demo Mode gains additive `llm_calls` persistence,
but the canonical domain output is unchanged — this is the "additive demo
llm_calls" + "Demo Mode requires no live credentials" test requirements.
`DemoLLMProvider` never touches `OPENAI_API_KEY`/the `openai` SDK at all.
"""

from __future__ import annotations

from tests.api_helpers import create_play, start_run, wait_for_terminal


async def test_demo_run_persists_one_llm_calls_row_per_logical_call(client, monkeypatch):
    # No OpenAI credentials configured anywhere in this test — Demo Mode
    # must still fully complete.
    monkeypatch.setattr("groundwork.config.settings.openai_api_key", None)

    play = await create_play(client)
    run = await start_run(client, play["id"])
    run = await wait_for_terminal(client, run["run_id"])
    assert run["status"] in {"COMPLETED", "PARTIAL"}

    r = await client.get(f"/api/runs/{run['id']}/evaluation")
    assert r.status_code == 200
    usage = r.json()["llm_usage"]
    assert usage["provider_attempts"] > 0
    assert usage["logical_calls"] > 0
    assert usage["by_status"] == {"OK": usage["provider_attempts"]}  # every demo attempt is INITIAL/OK
    # v2 §V2-F: `linkedin_personalization` is a new, additive operation —
    # RESOLVED LinkedIn prospects (Northwind, Sable) now also draft a
    # LinkedIn message via a separate LLM call.
    assert set(usage["by_operation"].keys()) <= {
        "research_extraction", "score_explanation", "personalization", "linkedin_personalization",
    }
    # No pricing is configured for Demo Mode's provider — cost stays null,
    # never a fabricated dollar figure.
    assert usage["estimated_cost_usd"] is None
