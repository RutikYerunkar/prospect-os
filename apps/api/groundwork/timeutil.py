"""UTC datetime helpers — Checkpoint I1 Phase 2.

SQLite has no native timestamptz type: SQLAlchemy's `DateTime(timezone=True)`
is accepted syntactically for cross-dialect portability, but on the sqlite
dialect it silently drops `tzinfo` on round-trip (verified directly against
this project's SQLAlchemy/aiosqlite versions — insert a tz-aware datetime,
read it back naive). Postgres's `timestamptz` preserves it. That mismatch is
exactly the "SQLite-hidden, Postgres-breaking" bug this phase fixes: code that
subtracts a freshly-constructed aware `now()` from a value just read out of
SQLite works by accident (both operands end up naive after enough hops), then
raises `TypeError: can't subtract offset-naive and offset-aware datetimes`
the moment the same code runs against Postgres.

Two rules going forward:
1. Every new "now" this app constructs goes through `utcnow()` here — never
   a bare `datetime.utcnow()` (deprecated in 3.12, and naive) or
   `datetime.now()` (local time).
2. Every comparison/subtraction between a freshly-constructed `utcnow()` and
   a datetime that came back from the database goes through `ensure_aware()`
   (or `elapsed_seconds()`, which does it for you) first — never assume the
   value's `tzinfo` matches what dialect you think you're running against.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """The one timezone-aware 'now' constructor for this app. Every
    `DateTime` column default (see `models/tables.py::_now`) and every
    hand-written repository write to a `*_at` column should use this."""
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime | None) -> datetime | None:
    """Normalizes a datetime that may have round-tripped through SQLite
    (naive, but always representing a UTC instant — this app never writes
    anything else) or Postgres (already aware) into one aware-UTC shape,
    safe to subtract or compare against `utcnow()` or another normalized
    value. `None` passes through — callers that need a "now" fallback
    should use `elapsed_seconds()` instead of manually defaulting."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def elapsed_seconds(start: datetime, end: datetime | None = None) -> float:
    """`(end or now) - start` in seconds, both sides normalized through
    `ensure_aware()` first. `end=None` means "now"."""
    end_aware = ensure_aware(end) if end is not None else utcnow()
    start_aware = ensure_aware(start)
    assert start_aware is not None  # callers must pass a real start
    return (end_aware - start_aware).total_seconds()
