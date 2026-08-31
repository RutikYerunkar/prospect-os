"""GET /runs/{id}/events?after_seq= — the durable, resumable SSE stream (§19).

These are the checkpoint's explicit reconnection-verification steps: connect,
record a cursor, disconnect mid-run, reconnect with `after_seq`, and confirm
no event is lost or replayed twice; then confirm a completed run replays its
full history from `after_seq=0`.
"""

from __future__ import annotations

import json

from tests.api_helpers import create_play, start_run, wait_for_terminal


async def _iter_sse_frames(response):
    buf = ""
    async for chunk in response.aiter_text():
        buf += chunk
        while "\n\n" in buf:
            frame, buf = buf.split("\n\n", 1)
            if not frame.strip() or frame.startswith(":"):
                continue  # heartbeat comment line
            parsed: dict = {}
            for line in frame.split("\n"):
                if line.startswith("id: "):
                    parsed["id"] = int(line[len("id: "):])
                elif line.startswith("event: "):
                    parsed["event"] = line[len("event: "):]
                elif line.startswith("data: "):
                    parsed["data"] = json.loads(line[len("data: "):])
            if parsed:
                yield parsed


async def _collect_all(client, run_id: str, after_seq: int, *, limit: int | None = None) -> list[dict]:
    frames: list[dict] = []
    async with client.stream(
        "GET", f"/api/runs/{run_id}/events", params={"after_seq": after_seq}, timeout=30.0
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for frame in _iter_sse_frames(resp):
            frames.append(frame)
            if limit is not None and len(frames) >= limit:
                break
    return frames


async def test_sse_emits_persisted_events_with_monotonic_seq_and_interleaving(client) -> None:
    play = await create_play(client)
    run = await start_run(client, play["id"])

    frames = await _collect_all(client, run["run_id"], after_seq=0)

    assert frames, "expected at least one SSE frame"
    seqs = [f["id"] for f in frames]
    assert seqs == sorted(seqs), "seq must be strictly increasing"
    assert len(seqs) == len(set(seqs)), "no seq should be replayed within one connection"

    # The stream only closes after the run is terminal (poll confirms it).
    final = await wait_for_terminal(client, run["run_id"], timeout=5.0)
    assert final["status"] != "RUNNING"

    # Interleaving: multiple prospects' steps are in flight before the first
    # one finishes — not one prospect run start-to-finish before the next starts.
    first_completed_idx = next(
        i for i, f in enumerate(frames) if f["event"] == "prospect.completed"
    )
    prospects_active_before_first_completion = {
        f["data"]["prospect_id"]
        for f in frames[:first_completed_idx]
        if f["data"]["prospect_id"] is not None and f["event"] in ("prospect.discovered", "step.started")
    }
    assert len(prospects_active_before_first_completion) > 1, (
        "expected independently-executing prospects to interleave before any one of them finishes"
    )


async def test_sse_reconnect_with_after_seq_loses_nothing_and_replays_nothing_twice(client) -> None:
    play = await create_play(client)
    run = await start_run(client, play["id"])
    run_id = run["run_id"]

    # Connect, read a handful of events, then disconnect early (simulates
    # `curl -N` getting killed) while the run is still executing. A 7-company
    # run produces on the order of a hundred events, so 15 events in is
    # reliably mid-run without a second, racy status round-trip to prove it —
    # the absence of a terminal run.* event in this batch is itself the proof.
    first_batch = await _collect_all(client, run_id, after_seq=0, limit=15)
    assert len(first_batch) == 15
    last_seq_seen = first_batch[-1]["id"]
    assert not any(f["event"] in ("run.completed", "run.failed") for f in first_batch), (
        "test is only meaningful if we disconnected before the run finished"
    )

    # Reconnect with the last cursor and drain until the run finishes.
    second_batch = await _collect_all(client, run_id, after_seq=last_seq_seen)
    await wait_for_terminal(client, run_id)

    second_seqs = [f["id"] for f in second_batch]
    assert all(seq > last_seq_seen for seq in second_seqs), "reconnect replayed an already-seen event"
    assert second_seqs == sorted(second_seqs)

    # No gap: the full history (first connection + reconnect) is contiguous
    # against a fresh from-zero replay of the now-completed run.
    full_replay = await _collect_all(client, run_id, after_seq=0)
    full_seqs = [f["id"] for f in full_replay]
    reconnected_seqs = [f["id"] for f in first_batch] + second_seqs
    assert reconnected_seqs == full_seqs


async def test_completed_run_replays_full_history_from_zero(client) -> None:
    play = await create_play(client)
    run = await start_run(client, play["id"])
    run_id = run["run_id"]

    await wait_for_terminal(client, run_id)

    replay_a = await _collect_all(client, run_id, after_seq=0)
    replay_b = await _collect_all(client, run_id, after_seq=0)

    assert replay_a, "expected a completed run to still have a replayable history"
    assert [f["id"] for f in replay_a] == [f["id"] for f in replay_b]
    assert any(f["event"] == "run.completed" for f in replay_a)


async def test_sse_unknown_run_is_404(client) -> None:
    r = await client.get("/api/runs/does-not-exist/events")
    assert r.status_code == 404
