"""`CORS_ORIGINS` mode="before" validator — Checkpoint I1 Phase 1. Accepts
both the JSON-list form pydantic-settings understands natively and a plain
comma-separated form that's easier to hand-type into a host's environment
variable UI.
"""

from __future__ import annotations

from groundwork.config import Settings


def test_cors_origins_default(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:3000"]


def test_cors_origins_json_list_form(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", '["http://a.example.com","http://b.example.com"]')
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://a.example.com", "http://b.example.com"]


def test_cors_origins_comma_separated_form(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a.example.com,http://b.example.com")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://a.example.com", "http://b.example.com"]


def test_cors_origins_comma_separated_form_trims_whitespace(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " http://a.example.com , http://b.example.com ")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://a.example.com", "http://b.example.com"]


def test_cors_origins_single_origin_comma_form():
    settings = Settings(_env_file=None, cors_origins="http://solo.example.com")
    assert settings.cors_origins == ["http://solo.example.com"]


def test_cors_origins_blank_env_is_empty_list(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == []


def test_cors_origins_python_list_passthrough():
    settings = Settings(_env_file=None, cors_origins=["http://direct.example.com"])
    assert settings.cors_origins == ["http://direct.example.com"]
