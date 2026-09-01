"""H1 Phase 2 — offline, public-suffix-aware domain normalization.

Every test in this file must pass with networking unavailable — see
`test_no_network_access_attempted` for the direct proof.
"""

from __future__ import annotations

import socket

import pytest

from groundwork.domain.psl import canonical_domain, decompose


def test_simple_com_domain() -> None:
    assert canonical_domain("https://www.Acme.COM/") == "acme.com"
    assert canonical_domain("acme.com") == "acme.com"


def test_co_uk_two_label_suffix_not_truncated() -> None:
    # A naive "everything after the last dot" normalizer would treat this as
    # `co.uk` (wrong — `co.uk` is the *suffix*, not the company identity).
    assert canonical_domain("acme.co.uk") == "acme.co.uk"
    assert canonical_domain("www.acme.co.uk") == "acme.co.uk"


def test_bare_public_suffix_rejected() -> None:
    assert canonical_domain("co.uk") is None
    assert canonical_domain("com") is None


def test_private_suffix_github_io_stays_distinct() -> None:
    # acme.github.io must remain its own registrable identity — collapsing
    # it to the bare `github.io` would merge every GitHub Pages site into
    # one "company."
    assert canonical_domain("acme.github.io") == "acme.github.io"
    other = canonical_domain("other.github.io")
    assert other == "other.github.io"
    assert canonical_domain("acme.github.io") != other


def test_bare_private_suffix_rejected() -> None:
    assert canonical_domain("github.io") is None


def test_case_and_trailing_dot_normalization() -> None:
    assert canonical_domain("ACME.COM.") == "acme.com"
    assert canonical_domain("Acme.Com") == "acme.com"


def test_idn_punycode_safe() -> None:
    # ASCII/punycode form.
    assert canonical_domain("xn--mnchen-3ya.de") == "xn--mnchen-3ya.de"
    # Native unicode form must not crash and must normalize deterministically.
    result = canonical_domain("münchen.de")
    assert result is not None
    assert result.endswith(".de")


def test_subdomain_collapses_to_registrable_domain() -> None:
    assert canonical_domain("app.eu.acme.com") == "acme.com"


def test_empty_and_unparseable_input() -> None:
    assert canonical_domain("") is None
    assert canonical_domain("   ") is None


def test_unlisted_reserved_tld_stays_distinct_not_collapsed() -> None:
    # RFC 2606 reserved TLDs (.example/.test/.invalid/.localhost) are
    # deliberately absent from the public suffix list. tldextract can't
    # determine a suffix boundary for them, so the fallback must not merge
    # two distinct hosts sharing an unrecognized TLD into one bare "domain".
    a = canonical_domain("alphacanary.example")
    b = canonical_domain("betacanary.example")
    assert a == "alphacanary.example"
    assert b == "betacanary.example"
    assert a != b


def test_deterministic_repeated_calls() -> None:
    results = {canonical_domain("acme.co.uk") for _ in range(20)}
    assert results == {"acme.co.uk"}


def test_decompose_exposes_private_flag() -> None:
    result = decompose("acme.github.io")
    assert result is not None
    assert result.is_private is True
    result2 = decompose("acme.com")
    assert result2 is not None
    assert result2.is_private is False


def test_no_network_access_attempted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct proof this module never touches the network: any attempt to
    open a socket during normalization raises, and normalization must still
    succeed for a battery of real-shaped inputs."""

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted during offline PSL normalization")

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)

    assert canonical_domain("acme.com") == "acme.com"
    assert canonical_domain("acme.co.uk") == "acme.co.uk"
    assert canonical_domain("acme.github.io") == "acme.github.io"
    assert canonical_domain("münchen.de") is not None
