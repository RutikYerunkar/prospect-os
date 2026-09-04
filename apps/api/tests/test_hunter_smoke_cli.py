"""V2-DH: `scripts/hunter_smoke.py`'s CLI surface — exactly `--person`/
`--i-understand-this-costs-money`, capped at one `--person`, and the email-
masking helper never leaks a raw address. Makes no real Hunter call and
never imports anything that would (the module's `main()`/`_run_one()` isn't
invoked here, only pure helpers and `parse_args()`).
"""

from __future__ import annotations

from groundwork.scripts.hunter_smoke import _mask_email, _parse_person, parse_args


def test_module_docstring_documents_the_real_cli() -> None:
    from groundwork.scripts import hunter_smoke

    doc = hunter_smoke.__doc__ or ""
    assert "--person" in doc
    assert "--i-understand-this-costs-money" in doc
    assert "HUNTER_API_KEY" in doc


def test_parser_only_defines_person_and_confirmation_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["hunter_smoke.py", "--i-understand-this-costs-money", "--person", "Jane Doe:example.com:VP of Sales"],
    )
    args = parse_args()
    assert args.confirmed is True
    assert args.person == "Jane Doe:example.com:VP of Sales"


def test_parse_person_splits_name_domain_title() -> None:
    person = _parse_person("Jane Doe:example.com:VP of Sales")
    assert person.full_name == "Jane Doe"
    assert person.company_domain == "example.com"
    assert person.title == "VP of Sales"


def test_parse_person_title_optional() -> None:
    person = _parse_person("Jane Doe:example.com")
    assert person.title is None


def test_parse_person_missing_required_arg_exits(monkeypatch) -> None:
    import pytest

    with pytest.raises(SystemExit):
        _parse_person(None)


def test_parse_person_malformed_exits() -> None:
    import pytest

    with pytest.raises(SystemExit):
        _parse_person("just-a-name")


def test_mask_email_hides_local_part_keeps_domain() -> None:
    masked = _mask_email("priya.natarajan@acme.example.com")
    assert masked.startswith("p")
    assert masked.endswith("@acme.example.com")
    assert "priya.natarajan" not in masked


def test_mask_email_handles_malformed_input() -> None:
    assert _mask_email("not-an-email") == "***"
