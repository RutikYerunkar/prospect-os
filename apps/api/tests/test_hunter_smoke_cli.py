"""V2-DH: `scripts/hunter_smoke.py`'s CLI surface — `--person`,
`--i-understand-this-costs-money` (real-key mode), and `--use-test-api-key`
(zero-cost contract-probe mode, §Part 16 of the frozen plan). Proves the two
modes are unambiguous: test-key mode never requires `HUNTER_API_KEY` or the
cost acknowledgment and never reads the developer's real configured key;
real-key mode is otherwise unchanged.

No automated test in this file makes an external request. `_build_runtime`
only ever constructs an `httpx.AsyncClient` (which opens no socket by
itself); the one test that exercises an actual HTTP exchange
(`test_test_api_key_never_appears_in_request_url`) wires a scripted
`httpx.MockTransport` via `tests/live_hunter_helpers.py`, exactly like every
other Hunter adapter test — it never reaches the real `api.hunter.io`.
`main()` itself is never invoked here (it would open a real client with no
transport override).
"""

from __future__ import annotations

import argparse

import pytest

from groundwork.config import settings
from groundwork.scripts.hunter_smoke import (
    _HUNTER_DOCUMENTED_TEST_API_KEY,
    _TestKeySettings,
    _build_runtime,
    _mask_email,
    _parse_person,
    _require_valid_mode,
    parse_args,
)
from tests.live_hunter_helpers import ScriptedHunterTransport, email_finder_response


def test_module_docstring_documents_both_modes() -> None:
    from groundwork.scripts import hunter_smoke

    doc = hunter_smoke.__doc__ or ""
    assert "--person" in doc
    assert "--i-understand-this-costs-money" in doc
    assert "--use-test-api-key" in doc
    assert "HUNTER_API_KEY" in doc
    assert "test-api-key" in doc


def test_parser_defines_person_confirmation_and_test_key_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["hunter_smoke.py", "--i-understand-this-costs-money", "--person", "Jane Doe:example.com:VP of Sales"],
    )
    args = parse_args()
    assert args.confirmed is True
    assert args.use_test_api_key is False
    assert args.person == "Jane Doe:example.com:VP of Sales"


def test_help_text_includes_use_test_api_key_flag(capsys, monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["hunter_smoke.py", "--help"])
    with pytest.raises(SystemExit):
        parse_args()
    captured = capsys.readouterr()
    assert "--use-test-api-key" in captured.out
    assert "--i-understand-this-costs-money" in captured.out
    assert "--person" in captured.out


def test_parser_accepts_use_test_api_key_without_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["hunter_smoke.py", "--use-test-api-key", "--person", "Jane Doe:example.com:VP of Sales"],
    )
    args = parse_args()
    assert args.use_test_api_key is True
    assert args.confirmed is False


# --- mode validation (_require_valid_mode) ---------------------------------


def test_require_valid_mode_allows_test_key_without_cost_acknowledgment() -> None:
    args = argparse.Namespace(use_test_api_key=True, confirmed=False)
    _require_valid_mode(args)  # must not raise


def test_require_valid_mode_allows_real_mode_with_confirmation() -> None:
    args = argparse.Namespace(use_test_api_key=False, confirmed=True)
    _require_valid_mode(args)  # must not raise


def test_require_valid_mode_refuses_real_mode_without_confirmation() -> None:
    args = argparse.Namespace(use_test_api_key=False, confirmed=False)
    with pytest.raises(SystemExit):
        _require_valid_mode(args)


# --- test-key mode never touches the real configured key -------------------


def test_test_key_settings_never_reads_the_configured_hunter_api_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "hunter_api_key", "sk-real-developer-secret")
    test_settings = _TestKeySettings()
    assert test_settings.hunter_api_key == _HUNTER_DOCUMENTED_TEST_API_KEY
    assert test_settings.hunter_api_key != "sk-real-developer-secret"


async def test_build_runtime_test_key_mode_does_not_require_hunter_api_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "hunter_api_key", None)
    runtime = _build_runtime(use_test_key=True)
    try:
        assert runtime is not None
        assert runtime.client.headers.get("X-API-KEY") == _HUNTER_DOCUMENTED_TEST_API_KEY
    finally:
        await runtime.close()


async def test_build_runtime_real_mode_requires_hunter_api_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "hunter_api_key", None)
    runtime = _build_runtime(use_test_key=False)
    assert runtime is None


async def test_build_runtime_real_mode_uses_the_configured_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "hunter_api_key", "sk-real-developer-secret")
    runtime = _build_runtime(use_test_key=False)
    try:
        assert runtime is not None
        assert runtime.client.headers.get("X-API-KEY") == "sk-real-developer-secret"
    finally:
        await runtime.close()


async def test_test_api_key_never_appears_in_request_url() -> None:
    """The one test in this file that issues an HTTP exchange — against a
    scripted `MockTransport`, never the real Hunter API."""
    import httpx

    from groundwork.scripts.hunter_smoke import _Person, _run_one

    transport = ScriptedHunterTransport([(200, email_finder_response())])
    http_client = httpx.AsyncClient(transport=transport)
    runtime = _build_runtime(use_test_key=True, http_client=http_client)
    try:
        assert runtime is not None
        await _run_one(runtime, _Person(full_name="Jane Doe", company_domain="example.com"))
    finally:
        await runtime.close()

    assert transport.calls == 1
    request = transport.requests[0]
    assert _HUNTER_DOCUMENTED_TEST_API_KEY not in str(request.url)
    assert request.headers.get("x-api-key") == _HUNTER_DOCUMENTED_TEST_API_KEY


# --- name parsing / masking (unchanged) -------------------------------------


def test_parse_person_splits_name_domain_title() -> None:
    person = _parse_person("Jane Doe:example.com:VP of Sales")
    assert person.full_name == "Jane Doe"
    assert person.company_domain == "example.com"
    assert person.title == "VP of Sales"


def test_parse_person_title_optional() -> None:
    person = _parse_person("Jane Doe:example.com")
    assert person.title is None


def test_parse_person_missing_required_arg_exits() -> None:
    with pytest.raises(SystemExit):
        _parse_person(None)


def test_parse_person_malformed_exits() -> None:
    with pytest.raises(SystemExit):
        _parse_person("just-a-name")


def test_mask_email_hides_local_part_keeps_domain() -> None:
    masked = _mask_email("priya.natarajan@acme.example.com")
    assert masked.startswith("p")
    assert masked.endswith("@acme.example.com")
    assert "priya.natarajan" not in masked


def test_mask_email_handles_malformed_input() -> None:
    assert _mask_email("not-an-email") == "***"
