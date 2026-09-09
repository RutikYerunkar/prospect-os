"""§3.8 — `domain/contact_identity.py::normalize_email_identity`.

Casefolding both parts, IDNA/unicode-vs-ASCII domain forms collapsing to one
key, plus-tags and dots explicitly NOT stripped, invalid inputs raise rather
than pass through, and idempotence.
"""

from __future__ import annotations

import pytest

from groundwork.domain.contact_identity import EMAIL_IDENTITY_VERSION, InvalidEmailIdentity, normalize_email_identity


def test_version_is_v1():
    assert EMAIL_IDENTITY_VERSION == "v1"


class TestCasefolding:
    def test_local_part_is_casefolded(self):
        assert normalize_email_identity("Priya@x.com") == normalize_email_identity("priya@x.com")

    def test_domain_is_casefolded(self):
        assert normalize_email_identity("priya@X.COM") == "priya@x.com"

    def test_mixed_case_both_parts(self):
        assert normalize_email_identity("Priya.Natarajan@NorthwindLabs.COM") == "priya.natarajan@northwindlabs.com"

    def test_sharp_s_casefold_not_lower(self):
        # `casefold()` maps German ß -> "ss"; `.lower()` would leave it as ß.
        # This proves normalize_email_identity uses casefold, not lower.
        assert normalize_email_identity("straße@ß.de").startswith("strasse@")


class TestUnicodeAndIdna:
    def test_unicode_domain_collapses_to_punycode(self):
        normalized = normalize_email_identity("user@café.com")
        assert normalized == "user@xn--caf-dma.com"

    def test_already_punycode_domain_is_stable(self):
        assert normalize_email_identity("user@xn--caf-dma.com") == "user@xn--caf-dma.com"

    def test_unicode_and_ascii_forms_collapse_to_one_key(self):
        assert normalize_email_identity("user@café.com") == normalize_email_identity("user@xn--caf-dma.com")

    def test_fullwidth_and_ascii_domain_collapse(self):
        # NFKC folds fullwidth ASCII variants to plain ASCII before IDNA.
        assert normalize_email_identity("user@ｅｘａｍｐｌｅ.com") == "user@example.com"

    def test_trailing_dot_on_domain_is_stripped(self):
        assert normalize_email_identity("user@example.com.") == "user@example.com"

    def test_whitespace_is_stripped(self):
        assert normalize_email_identity("  user@example.com  ") == "user@example.com"


class TestPlusTagsAndDotsRetained:
    def test_plus_tag_is_not_stripped(self):
        assert normalize_email_identity("priya+sales@northwindlabs.com") == "priya+sales@northwindlabs.com"
        assert normalize_email_identity("priya+sales@northwindlabs.com") != normalize_email_identity(
            "priya@northwindlabs.com"
        )

    def test_dots_in_local_part_are_not_stripped(self):
        assert normalize_email_identity("priya.natarajan@x.com") == "priya.natarajan@x.com"
        assert normalize_email_identity("priya.natarajan@x.com") != normalize_email_identity(
            "priyanatarajan@x.com"
        )


class TestInvalidFormsRaise:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "no-at-sign.example.com",
            "two@at@signs.com",
            "@missing-local.com",
            "missing-domain@",
            "   ",
            "user@.",
            "user@_invalid_.com",
        ],
    )
    def test_invalid_forms_raise(self, raw):
        with pytest.raises(InvalidEmailIdentity):
            normalize_email_identity(raw)

    def test_error_is_a_value_error_subclass(self):
        # Fail closed, never a silent pass-through: callers that only catch
        # ValueError still see it.
        with pytest.raises(ValueError):
            normalize_email_identity("not-an-email")


class TestIdempotence:
    @pytest.mark.parametrize(
        "raw",
        [
            "priya@x.com",
            "Priya.Natarajan+sales@NorthwindLabs.COM",
            "user@café.com",
            "user@xn--caf-dma.com",
            "  user@example.com.  ",
            "STRASSE@ß.de",
        ],
    )
    def test_normalize_is_idempotent(self, raw):
        once = normalize_email_identity(raw)
        twice = normalize_email_identity(once)
        assert once == twice
