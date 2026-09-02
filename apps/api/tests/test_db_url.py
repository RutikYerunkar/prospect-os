"""`DATABASE_URL` normalization — Checkpoint I1 Phase 1.

Covers: sqlite passthrough, postgres/postgresql/postgresql+asyncpg scheme
normalization, deliberate sslmode/channel_binding handling, and fail-loud
behavior for unknown query parameters and malformed URLs.
"""

from __future__ import annotations

import pytest

from groundwork.db_url import DatabaseConfigurationError, normalize_database_url


def test_sqlite_aiosqlite_passthrough():
    url, connect_args = normalize_database_url("sqlite+aiosqlite:///./groundwork.db")
    assert url == "sqlite+aiosqlite:///./groundwork.db"
    assert connect_args == {}


def test_sqlite_bare_scheme_normalized_to_aiosqlite():
    url, connect_args = normalize_database_url("sqlite:///./groundwork.db")
    assert url == "sqlite+aiosqlite:///./groundwork.db"
    assert connect_args == {}


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+asyncpg"])
def test_postgres_schemes_normalize_to_asyncpg(scheme):
    url, connect_args = normalize_database_url(f"{scheme}://user:pass@host:5432/dbname")
    assert url == "postgresql+asyncpg://user:pass@host:5432/dbname"
    assert connect_args == {}


@pytest.mark.parametrize(
    "sslmode", ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
)
def test_sslmode_maps_to_asyncpg_ssl_connect_arg(sslmode):
    url, connect_args = normalize_database_url(
        f"postgresql://user:pass@host/dbname?sslmode={sslmode}"
    )
    assert url == "postgresql+asyncpg://user:pass@host/dbname"
    assert connect_args == {"ssl": sslmode}


def test_sslmode_unknown_value_raises_actionable_error():
    with pytest.raises(DatabaseConfigurationError, match="sslmode"):
        normalize_database_url("postgresql://user:pass@host/dbname?sslmode=bogus")


@pytest.mark.parametrize("value", ["prefer", "disable"])
def test_channel_binding_safe_values_are_dropped(value):
    url, connect_args = normalize_database_url(
        f"postgresql://user:pass@host/dbname?channel_binding={value}"
    )
    assert url == "postgresql+asyncpg://user:pass@host/dbname"
    assert connect_args == {}


def test_channel_binding_require_raises_actionable_error():
    with pytest.raises(DatabaseConfigurationError, match="channel_binding=require"):
        normalize_database_url("postgresql://user:pass@host/dbname?channel_binding=require")


def test_channel_binding_unknown_value_raises():
    with pytest.raises(DatabaseConfigurationError, match="channel_binding"):
        normalize_database_url("postgresql://user:pass@host/dbname?channel_binding=bogus")


def test_sslmode_and_channel_binding_combined_neon_style_url():
    url, connect_args = normalize_database_url(
        "postgresql://user:pass@ep-cool-name.us-east-2.aws.neon.tech/dbname"
        "?sslmode=require&channel_binding=prefer"
    )
    assert url == "postgresql+asyncpg://user:pass@ep-cool-name.us-east-2.aws.neon.tech/dbname"
    assert connect_args == {"ssl": "require"}


def test_unknown_query_parameter_raises_actionable_error():
    with pytest.raises(DatabaseConfigurationError, match="options"):
        normalize_database_url("postgresql://user:pass@host/dbname?options=-c%20timezone%3DUTC")


def test_empty_url_raises():
    with pytest.raises(DatabaseConfigurationError):
        normalize_database_url("")


def test_unsupported_scheme_raises():
    with pytest.raises(DatabaseConfigurationError, match="scheme"):
        normalize_database_url("mysql://user:pass@host/dbname")
