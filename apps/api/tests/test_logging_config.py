"""Checkpoint I1 Phase 9C: `JsonFormatter` — valid JSON, redaction, and
contextual `extra=` fields end to end, independent of any real handler
being wired via `configure_logging()`.
"""

from __future__ import annotations

import json
import logging

from groundwork.logging_config import JsonFormatter

SENTINEL = "sk-THIS-IS-A-CANARY-SECRET-1234567890abcdef"


def _make_record(msg: str, *, level: int = logging.INFO, extra: dict | None = None) -> logging.LogRecord:
    record = logging.LogRecord(
        name="groundwork.test", level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def test_json_formatter_produces_valid_json():
    record = _make_record("hello world")
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "groundwork.test"
    assert "timestamp" in payload
    assert "environment" in payload


def test_json_formatter_redacts_secret_in_message(monkeypatch):
    monkeypatch.setattr("groundwork.config.settings.openai_api_key", SENTINEL)
    record = _make_record(f"upstream error: {SENTINEL}")
    payload = json.loads(JsonFormatter().format(record))
    assert SENTINEL not in payload["message"]


def test_json_formatter_includes_contextual_fields_only_when_present():
    record = _make_record("run event", extra={"run_id": "r1", "prospect_id": "p1"})
    payload = json.loads(JsonFormatter().format(record))
    assert payload["run_id"] == "r1"
    assert payload["prospect_id"] == "p1"
    assert "executor_id" not in payload
    assert "request_id" not in payload
    assert "latency_ms" not in payload


def test_json_formatter_includes_redacted_exception_traceback(monkeypatch):
    monkeypatch.setattr("groundwork.config.settings.openai_api_key", SENTINEL)
    try:
        raise ValueError(f"boom {SENTINEL}")
    except ValueError:
        import sys

        record = _make_record("failure", level=logging.ERROR)
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter().format(record))
    assert "exception" in payload
    assert SENTINEL not in payload["exception"]
    assert "ValueError" in payload["exception"]
