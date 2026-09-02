"""Phase 11 security: a sentinel secret deliberately echoed by a fake
provider error must appear nowhere observable — `llm_calls.error_message`,
`llm_calls.validation_error`, or anywhere in the run's evaluation payload.
"""

from __future__ import annotations

from groundwork.observability.redact import redact

SENTINEL = "sk-THIS-IS-A-CANARY-SECRET-1234567890abcdef"


def test_redact_strips_configured_secret(monkeypatch):
    monkeypatch.setattr("groundwork.config.settings.openai_api_key", SENTINEL)
    text = f"AuthenticationError: invalid key {SENTINEL} rejected by upstream"
    out = redact(text)
    assert SENTINEL not in out
    assert "[REDACTED]" in out


def test_redact_strips_generic_secret_shaped_token_even_if_unconfigured(monkeypatch):
    monkeypatch.setattr("groundwork.config.settings.openai_api_key", None)
    text = "upstream echoed back Authorization: Bearer sk-abcdefghijklmnopqrstuvwx in its error body"
    out = redact(text)
    assert "sk-abcdefghijklmnopqrstuvwx" not in out


def test_redact_truncates_long_payloads():
    out = redact("x" * 5000)
    assert len(out) < 2100


def test_redact_none_passthrough():
    assert redact(None) is None


async def test_llm_calls_error_message_is_redacted_end_to_end(monkeypatch, session_factory):
    """A ProviderError whose message contains the sentinel must come out of
    `llm_calls.record_attempts` with the sentinel scrubbed — the real
    end-to-end path a live provider's raw exception text would take.

    Uses real Play/Run/Company/Prospect rows (not made-up ids) so this
    exercises `llm_calls.run_id`/`.prospect_id`'s actual foreign keys under
    `PRAGMA foreign_keys=ON`, same as production — a made-up `run_id`/
    `prospect_id` here previously passed only because the test fixture had
    FK enforcement off (see `conftest.py::_enable_wal`'s docstring).
    """
    from datetime import datetime, timezone

    from groundwork.models.schemas import CompanySeed
    from groundwork.providers.base import LLMAttemptKind, LLMAttemptStatus, LLMAttemptTelemetry
    from groundwork.repositories.llm_calls import LLMCallRepository
    from groundwork.repositories.plays import PlayRepository
    from groundwork.repositories.prospects import CompanyRepository, ProspectRepository
    from groundwork.repositories.runs import RunRepository

    monkeypatch.setattr("groundwork.config.settings.openai_api_key", SENTINEL)

    plays = PlayRepository(session_factory)
    runs = RunRepository(session_factory)
    companies = CompanyRepository(session_factory)
    prospects = ProspectRepository(session_factory)

    play_id = await plays.create(name="t", objective_text="t", icp_spec={}, mode="live")
    run_id = await runs.create(play_id=play_id, mode="live", seed=1)
    company = CompanySeed(slug="acme", name="Acme", domain="acme.example", industry="x", size_band="1-10", employee_count=5)
    company_id = await companies.get_or_create(company, canonical_domain="acme.example", normalized_name="acme")
    prospect_id = await prospects.create(run_id=run_id, company_id=company_id, dedupe_key="acme.example", duplicate_of=None, status="RUNNING")

    repo = LLMCallRepository(session_factory)
    now = datetime.now(timezone.utc)
    attempt = LLMAttemptTelemetry(
        attempt=1, attempt_kind=LLMAttemptKind.INITIAL, schema_round=0, transport_retry_index=0,
        status=LLMAttemptStatus.AUTH_ERROR, started_at=now, finished_at=now, latency_ms=1.0,
        model="gpt-5.6-terra", input_digest="deadbeef",
        error_message=f"401 from upstream, echoed request had key={SENTINEL}",
        validation_error=f"schema mismatch near key {SENTINEL}",
    )
    await repo.record_attempts(
        call_group_id="grp-1", operation="score_explanation", provider="openai", prompt_version="live-v1",
        attempts=[attempt], run_id=run_id, prospect_id=prospect_id, step_name="score",
    )
    rows = await repo.for_run(run_id)
    assert len(rows) == 1
    assert SENTINEL not in (rows[0].error_message or "")
    assert SENTINEL not in (rows[0].validation_error or "")


async def test_agent_tasks_error_message_is_redacted_end_to_end(monkeypatch, session_factory):
    """Checkpoint I1 Phase 9 — closes the gap `test_llm_calls_error_message_
    is_redacted_end_to_end` above didn't cover: `agent_tasks.error_message`
    (one row per step *attempt*, written by `engine/step.py` via
    `TaskRepository.record`) used to persist a raw `str(exception)` with no
    redaction at all."""
    from groundwork.models.schemas import CompanySeed
    from groundwork.repositories.plays import PlayRepository
    from groundwork.repositories.prospects import CompanyRepository, ProspectRepository
    from groundwork.repositories.runs import RunRepository
    from groundwork.repositories.tasks import TaskRepository

    monkeypatch.setattr("groundwork.config.settings.openai_api_key", SENTINEL)
    tasks = TaskRepository(session_factory)
    plays = PlayRepository(session_factory)
    runs = RunRepository(session_factory)
    companies = CompanyRepository(session_factory)
    prospects = ProspectRepository(session_factory)

    play_id = await plays.create(name="t", objective_text="t", icp_spec={}, mode="live")
    run_id = await runs.create(play_id=play_id, mode="live", seed=1)
    company = CompanySeed(
        slug="acme", name="Acme", domain="acme.example", industry="x", size_band="1-10", employee_count=5
    )
    company_id = await companies.get_or_create(company, canonical_domain="acme.example", normalized_name="acme")
    prospect_id = await prospects.create(
        run_id=run_id, company_id=company_id, dedupe_key="acme.example", duplicate_of=None, status="RUNNING"
    )

    await tasks.record(
        run_id=run_id, prospect_id=prospect_id, step_name="research", attempt=1, status="FAILED",
        duration_ms=1.0, error_type="AuthenticationError",
        error_message=f"401 from upstream, echoed request had key={SENTINEL}",
    )

    rows = await tasks.for_run(run_id)
    assert len(rows) == 1
    assert SENTINEL not in (rows[0].error_message or "")
    assert "[REDACTED]" in (rows[0].error_message or "")


async def test_runs_error_is_redacted_end_to_end(monkeypatch, session_factory):
    """Checkpoint I1 Phase 9 — `runs.error` (set from `api/run_service.py`'s
    catch-all on a run that blows up before/around the per-prospect fan-out)
    used to persist `str(exception)` unredacted."""
    from groundwork.repositories.plays import PlayRepository
    from groundwork.repositories.runs import RunRepository

    monkeypatch.setattr("groundwork.config.settings.openai_api_key", SENTINEL)
    plays = PlayRepository(session_factory)
    runs = RunRepository(session_factory)

    play_id = await plays.create(name="t", objective_text="t", icp_spec={}, mode="live")
    run_id = await runs.create(play_id=play_id, mode="live", seed=1)

    await runs.finalize(
        run_id, status="PARTIAL", counters={},
        error=f"discovery failed: upstream rejected key={SENTINEL}",
    )

    row = await runs.get(run_id)
    assert SENTINEL not in (row.error or "")
    assert "[REDACTED]" in (row.error or "")


async def test_runs_error_is_redacted_via_finalize_owned(monkeypatch, session_factory):
    from groundwork.repositories.plays import PlayRepository
    from groundwork.repositories.runs import RunRepository

    monkeypatch.setattr("groundwork.config.settings.openai_api_key", SENTINEL)
    plays = PlayRepository(session_factory)
    runs = RunRepository(session_factory)

    play_id = await plays.create(name="t", objective_text="t", icp_spec={}, mode="live")
    run_id = await runs.create(play_id=play_id, mode="live", seed=1, executor_id="executor-1")

    owned = await runs.finalize_owned(
        run_id, "executor-1", status="PARTIAL", counters={},
        error=f"discovery failed: upstream rejected key={SENTINEL}",
    )
    assert owned is True

    row = await runs.get(run_id)
    assert SENTINEL not in (row.error or "")
    assert "[REDACTED]" in (row.error or "")


async def test_prospects_error_is_redacted_end_to_end(monkeypatch, session_factory):
    from groundwork.models.schemas import CompanySeed
    from groundwork.repositories.plays import PlayRepository
    from groundwork.repositories.prospects import CompanyRepository, ProspectRepository
    from groundwork.repositories.runs import RunRepository

    monkeypatch.setattr("groundwork.config.settings.openai_api_key", SENTINEL)
    plays = PlayRepository(session_factory)
    runs = RunRepository(session_factory)
    companies = CompanyRepository(session_factory)
    prospects = ProspectRepository(session_factory)

    play_id = await plays.create(name="t", objective_text="t", icp_spec={}, mode="live")
    run_id = await runs.create(play_id=play_id, mode="live", seed=1)
    company = CompanySeed(
        slug="acme", name="Acme", domain="acme.example", industry="x", size_band="1-10", employee_count=5
    )
    company_id = await companies.get_or_create(company, canonical_domain="acme.example", normalized_name="acme")
    prospect_id = await prospects.create(
        run_id=run_id, company_id=company_id, dedupe_key="acme.example", duplicate_of=None, status="RUNNING"
    )

    await prospects.finalize(prospect_id, status="FAILED", error=f"pipeline blew up: key={SENTINEL}")

    row = await prospects.get(prospect_id)
    assert SENTINEL not in (row.error or "")
    assert "[REDACTED]" in (row.error or "")
