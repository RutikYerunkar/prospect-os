"""V2-D follow-up: `scripts/enrichment_smoke.py`'s module docstring (also its
argparse `description`, via `RawDescriptionHelpFormatter`) must document the
CLI it actually implements — a stale `--full-name`/`--company-domain`
example was left over from an earlier draft while the real parser only ever
defined `--person`/`--i-understand-this-costs-money`. Guards against that
drifting again; makes no real Apollo call and never imports anything that
would (the module's `main()` isn't invoked here, only `parse_args()`).
"""

from __future__ import annotations

from groundwork.scripts.enrichment_smoke import parse_args


def test_module_docstring_matches_the_real_cli() -> None:
    from groundwork.scripts import enrichment_smoke

    doc = enrichment_smoke.__doc__ or ""
    assert "--full-name" not in doc
    assert "--company-domain" not in doc
    assert "--person" in doc
    assert "--i-understand-this-costs-money" in doc


def test_parser_only_defines_person_and_confirmation_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["enrichment_smoke.py", "--i-understand-this-costs-money", "--person", "Jane Doe:example.com:VP of Sales"],
    )
    args = parse_args()
    assert args.confirmed is True
    assert args.person == ["Jane Doe:example.com:VP of Sales"]
