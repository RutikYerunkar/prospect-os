"""Post-smoke-test hardening: a normal user who copies `.env.example` to
`.env` verbatim gets blank optional numeric assignments
(`OPENAI_PRICE_INPUT_USD_PER_MTOK=`, `OPENAI_PRICE_OUTPUT_USD_PER_MTOK=`,
`LIVE_RUN_SOFT_BUDGET_USD=`) — Pydantic's own float parsing rejects `""`
outright, so `Settings()` construction must not crash on it. Reproduces the
exact crash the first real live smoke test hit, and proves the intended
"unset -> None -> cost stays null / threshold unenforceable" semantics
still hold with a blank string, not just a genuinely absent env var.
"""

from __future__ import annotations

import pytest

from groundwork.config import Settings

BLANK_ENV = {
    "OPENAI_PRICE_INPUT_USD_PER_MTOK": "",
    "OPENAI_PRICE_OUTPUT_USD_PER_MTOK": "",
    "LIVE_RUN_SOFT_BUDGET_USD": "",
}


def test_settings_construction_does_not_crash_on_blank_optional_floats(monkeypatch):
    for key, value in BLANK_ENV.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)  # env-only, ignore any real .env on disk
    assert settings.openai_price_input_usd_per_mtok is None
    assert settings.openai_price_output_usd_per_mtok is None
    assert settings.live_run_soft_budget_usd is None


def test_settings_blank_whitespace_only_also_normalizes_to_none(monkeypatch):
    monkeypatch.setenv("OPENAI_PRICE_INPUT_USD_PER_MTOK", "   ")
    settings = Settings(_env_file=None)
    assert settings.openai_price_input_usd_per_mtok is None


@pytest.mark.parametrize(
    "field,env_key,raw",
    [
        ("openai_price_input_usd_per_mtok", "OPENAI_PRICE_INPUT_USD_PER_MTOK", "5.5"),
        ("openai_price_output_usd_per_mtok", "OPENAI_PRICE_OUTPUT_USD_PER_MTOK", "15.0"),
        ("live_run_soft_budget_usd", "LIVE_RUN_SOFT_BUDGET_USD", "2.5"),
    ],
)
def test_settings_real_numeric_values_still_parse(monkeypatch, field, env_key, raw):
    monkeypatch.setenv(env_key, raw)
    settings = Settings(_env_file=None)
    assert getattr(settings, field) == float(raw)


def test_settings_field_absent_entirely_still_defaults_to_none(monkeypatch):
    # No env var set at all (the pre-Checkpoint-G-docs case) — must behave
    # identically to a blank assignment, not require the key to be present.
    monkeypatch.delenv("OPENAI_PRICE_INPUT_USD_PER_MTOK", raising=False)
    monkeypatch.delenv("OPENAI_PRICE_OUTPUT_USD_PER_MTOK", raising=False)
    monkeypatch.delenv("LIVE_RUN_SOFT_BUDGET_USD", raising=False)
    settings = Settings(_env_file=None)
    assert settings.openai_price_input_usd_per_mtok is None
    assert settings.openai_price_output_usd_per_mtok is None
    assert settings.live_run_soft_budget_usd is None
