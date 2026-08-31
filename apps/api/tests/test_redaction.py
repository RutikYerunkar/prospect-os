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
