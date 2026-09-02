"""`groundwork.timeutil` — Checkpoint I1 Phase 2. Proves `ensure_aware`/
`elapsed_seconds` behave identically whether the input already has tzinfo
(as Postgres/timestamptz returns) or not (as SQLite's `DateTime(timezone=True)`
returns after round-tripping — see the module docstring)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from groundwork.timeutil import elapsed_seconds, ensure_aware, utcnow


def test_utcnow_is_timezone_aware():
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_ensure_aware_on_naive_datetime_assumes_utc():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    aware = ensure_aware(naive)
    assert aware.tzinfo is not None
    assert aware.utcoffset() == timedelta(0)
    assert aware.replace(tzinfo=None) == naive


def test_ensure_aware_on_aware_datetime_normalizes_to_utc():
    eastern = timezone(timedelta(hours=-5))
    aware = datetime(2026, 1, 1, 7, 0, 0, tzinfo=eastern)
    normalized = ensure_aware(aware)
    assert normalized.tzinfo == timezone.utc
    assert normalized == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_ensure_aware_none_passes_through():
    assert ensure_aware(None) is None


def test_elapsed_seconds_naive_start_naive_end():
    # Simulates two values that both round-tripped through SQLite (naive).
    start = datetime(2026, 1, 1, 12, 0, 0)
    end = datetime(2026, 1, 1, 12, 0, 30)
    assert elapsed_seconds(start, end) == 30.0


def test_elapsed_seconds_aware_start_aware_end():
    # Simulates two values read back from Postgres (aware).
    start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 12, 0, 45, tzinfo=timezone.utc)
    assert elapsed_seconds(start, end) == 45.0


def test_elapsed_seconds_mixed_naive_start_aware_end_does_not_raise():
    # The exact failure mode this phase fixes: a naive DB value compared
    # against a freshly-constructed aware "now" (or vice versa) must not
    # raise TypeError.
    naive_start = datetime(2026, 1, 1, 12, 0, 0)
    aware_end = datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)
    assert elapsed_seconds(naive_start, aware_end) == 60.0


def test_elapsed_seconds_end_none_uses_now():
    start = utcnow() - timedelta(seconds=5)
    result = elapsed_seconds(start, None)
    assert result >= 5.0
    assert result < 5.0 + 5.0  # generous upper bound, avoids CI flakiness
