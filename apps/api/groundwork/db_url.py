"""`DATABASE_URL` normalization — the one seam that decides which SQLAlchemy
dialect/driver actually runs, and what happens to managed-Postgres-specific
query parameters (`sslmode`, `channel_binding`) that show up in copy-pasted
connection strings (Neon, Render, Railway, ...).

Design constraints (Checkpoint I1 Phase 1):
- Accepts `sqlite+aiosqlite:///...`, `postgres://...`, `postgresql://...`,
  `postgresql+asyncpg://...` — always normalizes to a driver-qualified URL so
  `create_async_engine()` never has to guess a dialect.
- `sslmode`/`channel_binding` are handled *deliberately*, not passed through
  blind and not silently dropped — see `_map_sslmode`/`_handle_channel_binding`
  below for exactly what each value does and why.
- Any other query parameter, on any dialect, raises `DatabaseConfigurationError`
  rather than being silently discarded — a copy-pasted connection string with
  a parameter this app doesn't understand is a configuration bug, not a thing
  to ignore.
- A malformed URL (unparseable, unknown scheme) raises the same error type
  with an actionable message — this is meant to fail loudly at startup, not
  deep inside a request three hours later.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit, urlunsplit

# Mirrors asyncpg's own `SSLMode` enum values (`asyncpg/connect_utils.py`) —
# asyncpg's `ssl` connect kwarg accepts these exact strings directly (verified
# against the installed asyncpg==0.31.0: `connect_utils.py` calls
# `SSLMode.parse(ssl)` when `ssl` is a `str`), so no translation is needed
# beyond renaming the query key from `sslmode` (libpq/psycopg convention,
# what every managed-Postgres connection string uses) to `ssl` (asyncpg's
# actual connect kwarg name).
_ASYNCPG_SSLMODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}

# asyncpg hard-codes channel binding *off* in its SCRAM client-first message
# (see `asyncpg/protocol/scram.pyx`: "channel binding is turned off for the
# time being") — it never negotiates SCRAM-SHA-256-PLUS, full stop, in any
# version through 0.31.0. That makes `channel_binding=require` a promise we
# cannot keep: libpq treats `require` as "refuse to connect unless channel
# binding is actually used," and asyncpg has no code path that uses it. Rather
# than silently connect *without* the guarantee the URL asked for, we raise.
# `prefer` (Neon's own default) and `disable` both explicitly tolerate no
# channel binding, so both are safe to drop — asyncpg's fixed behavior already
# satisfies them.
_CHANNEL_BINDING_SAFE_TO_DROP = {"prefer", "disable"}


class DatabaseConfigurationError(ValueError):
    """Raised for a `DATABASE_URL` that is malformed or carries a query
    parameter this app doesn't know how to honor. Meant to fail application
    startup with an actionable message, not surface as an opaque traceback
    from inside `create_async_engine()` or a live request."""


def _map_sslmode(value: str) -> str:
    if value not in _ASYNCPG_SSLMODES:
        raise DatabaseConfigurationError(
            f"DATABASE_URL sslmode={value!r} is not a value asyncpg understands. "
            f"Supported: {', '.join(sorted(_ASYNCPG_SSLMODES))}."
        )
    return value


def _handle_channel_binding(value: str) -> None:
    if value in _CHANNEL_BINDING_SAFE_TO_DROP:
        return
    if value == "require":
        raise DatabaseConfigurationError(
            "DATABASE_URL channel_binding=require cannot be honored: asyncpg "
            "never negotiates SCRAM channel binding (it hard-codes it off), so "
            "this app cannot make the guarantee that parameter is asking for. "
            "Use channel_binding=prefer (safe — asyncpg's behavior already "
            "satisfies it) or drop the parameter entirely."
        )
    raise DatabaseConfigurationError(
        f"DATABASE_URL channel_binding={value!r} is not a recognized value. "
        "Supported: prefer, disable, require (rejected — see above)."
    )


def normalize_database_url(raw_url: str) -> tuple[str, dict]:
    """Returns `(normalized_url, connect_args)`.

    `normalized_url` is always driver-qualified (`sqlite+aiosqlite://` or
    `postgresql+asyncpg://`) and carries no query string — every query
    parameter has already been translated into `connect_args` (Postgres) or
    rejected. Pass `connect_args` straight through to
    `create_async_engine(url, connect_args=connect_args)`.
    """
    if not raw_url or not raw_url.strip():
        raise DatabaseConfigurationError("DATABASE_URL is empty.")

    try:
        parts = urlsplit(raw_url)
    except ValueError as exc:
        raise DatabaseConfigurationError(f"DATABASE_URL could not be parsed: {exc}") from exc

    scheme = parts.scheme.lower()

    if scheme in ("sqlite", "sqlite+aiosqlite"):
        # SQLite connection strings in this project never carry query
        # parameters that need translation (WAL/foreign_keys are enabled via
        # `db.py`'s PRAGMA listener, not the URL). Normalize the scheme only
        # via a plain string swap — `urlsplit`/`urlunsplit` collapse the
        # `///` (empty-netloc) form SQLite URLs rely on for a relative path
        # (`sqlite+aiosqlite:///./x.db`) down to `sqlite+aiosqlite:/./x.db`,
        # silently changing a relative path's meaning.
        _, _, rest = raw_url.partition(":")
        return f"sqlite+aiosqlite:{rest}", {}

    if scheme in ("postgres", "postgresql", "postgresql+asyncpg"):
        connect_args: dict = {}
        try:
            query_pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=False)
        except ValueError as exc:
            raise DatabaseConfigurationError(
                f"DATABASE_URL query string could not be parsed: {exc}"
            ) from exc

        for key, value in query_pairs:
            if key == "sslmode":
                connect_args["ssl"] = _map_sslmode(value)
            elif key == "channel_binding":
                _handle_channel_binding(value)
            else:
                raise DatabaseConfigurationError(
                    f"DATABASE_URL query parameter {key!r} is not supported. "
                    "Recognized parameters: sslmode, channel_binding. Remove it, "
                    "or extend groundwork/db_url.py to handle it deliberately "
                    "rather than have it silently ignored."
                )

        normalized = urlunsplit(("postgresql+asyncpg", parts.netloc, parts.path, "", parts.fragment))
        return normalized, connect_args

    raise DatabaseConfigurationError(
        f"DATABASE_URL scheme {parts.scheme!r} is not supported. "
        "Supported: sqlite+aiosqlite://, postgres://, postgresql://, postgresql+asyncpg://."
    )
