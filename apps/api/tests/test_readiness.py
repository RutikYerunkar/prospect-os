"""Checkpoint I1 Phase 9B — `GET /api/health` (liveness only, never queries
the DB) and `GET /api/ready` (real DB query + Alembic schema-currency +
provider CONFIG only, never a live OpenAI/Tavily call).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from groundwork.config import settings


async def test_health_never_queries_the_database(client):
    """Patches the module-level `engine` so any DB access would raise —
    `/api/health` must not touch it at all."""
    with patch("groundwork.main.engine") as mock_engine:
        mock_engine.connect.side_effect = AssertionError("health must never touch the database")
        r = await client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        mock_engine.connect.assert_not_called()


async def test_ready_reports_ok_against_the_sqlite_test_database(client):
    r = await client.get("/api/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert "sqlite" in body["checks"]["schema"]


async def test_ready_reports_provider_config_without_a_live_call(client, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-not-a-real-key")
    monkeypatch.setattr(settings, "tavily_api_key", None)
    r = await client.get("/api/ready")
    assert r.status_code == 200
    providers = r.json()["checks"]["providers"]
    assert providers["openai_configured"] is True
    assert providers["tavily_configured"] is False


async def test_ready_returns_503_when_database_unreachable(client):
    with patch("groundwork.main.engine") as mock_engine:
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = RuntimeError("connection refused")
        mock_engine.connect.return_value.__aenter__.return_value = mock_conn
        mock_engine.connect.return_value.__aexit__.return_value = False

        r = await client.get("/api/ready")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["database"] == "unreachable"
        assert body["checks"]["schema"] == "unknown"


async def test_ready_returns_503_when_schema_behind_head(client):
    with patch("groundwork.main.schema_upgrade_problems", new=AsyncMock(return_value=["schema is behind head"])):
        with patch("groundwork.main.engine") as mock_engine:
            mock_engine.dialect.name = "postgresql"
            mock_conn = AsyncMock()
            mock_engine.connect.return_value.__aenter__.return_value = mock_conn
            mock_engine.connect.return_value.__aexit__.return_value = False

            r = await client.get("/api/ready")
            assert r.status_code == 503
            body = r.json()
            assert body["checks"]["schema"] == "behind"
