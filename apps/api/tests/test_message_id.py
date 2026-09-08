"""`domain/message_id.py` (V2-I-b, Phase 4) — Message-ID generation and
reconciliation header matching."""

from __future__ import annotations

import pytest

from groundwork.domain.message_id import InvalidSenderDomain, generate_message_id_header, message_id_matches


def test_generation_is_deterministic():
    a = generate_message_id_header(
        execution_id="exec-1", idempotency_key="idem-1", sender_identifier="operator@example.com"
    )
    b = generate_message_id_header(
        execution_id="exec-1", idempotency_key="idem-1", sender_identifier="operator@example.com"
    )
    assert a == b


def test_generation_varies_with_execution_id():
    a = generate_message_id_header(
        execution_id="exec-1", idempotency_key="idem-1", sender_identifier="operator@example.com"
    )
    b = generate_message_id_header(
        execution_id="exec-2", idempotency_key="idem-1", sender_identifier="operator@example.com"
    )
    assert a != b


def test_uses_registrable_domain_of_sender():
    header = generate_message_id_header(
        execution_id="exec-1", idempotency_key="idem-1", sender_identifier="operator@mail.example.com"
    )
    assert header.endswith("@example.com>")


def test_shape_is_angle_bracketed_rfc_5322():
    header = generate_message_id_header(
        execution_id="exec-1", idempotency_key="idem-1", sender_identifier="operator@example.com"
    )
    assert header.startswith("<")
    assert header.endswith(">")
    assert "@" in header


def test_invalid_sender_domain_raises_no_fallback_host():
    # A bare public suffix with no registrable second-level label — resolves
    # to an empty registrable domain (`domain/psl.py`), never a fallback host.
    with pytest.raises(InvalidSenderDomain):
        generate_message_id_header(execution_id="exec-1", idempotency_key="idem-1", sender_identifier="operator@co.uk")


def test_missing_at_sign_raises():
    with pytest.raises(InvalidSenderDomain):
        generate_message_id_header(execution_id="exec-1", idempotency_key="idem-1", sender_identifier="not-an-email")


class TestMessageIdMatching:
    def test_matches_message_id_header_exactly(self):
        expected = "<abc@example.com>"
        assert message_id_matches(expected, message_id="<abc@example.com>", x_google_original_message_id=None)

    def test_matches_x_google_original_message_id(self):
        expected = "<abc@example.com>"
        assert message_id_matches(expected, message_id=None, x_google_original_message_id="<abc@example.com>")

    def test_matches_either_header_when_both_present_and_only_one_correct(self):
        expected = "<abc@example.com>"
        assert message_id_matches(
            expected, message_id="<different@example.com>", x_google_original_message_id="<abc@example.com>"
        )

    def test_no_match_when_neither_header_matches(self):
        expected = "<abc@example.com>"
        assert not message_id_matches(
            expected, message_id="<x@example.com>", x_google_original_message_id="<y@example.com>"
        )

    def test_conservative_whitespace_normalization(self):
        expected = "<abc@example.com>"
        assert message_id_matches(expected, message_id="  <abc@example.com>  ", x_google_original_message_id=None)

    def test_conservative_angle_bracket_normalization(self):
        expected = "<abc@example.com>"
        # Gmail may echo the header without angle brackets.
        assert message_id_matches(expected, message_id="abc@example.com", x_google_original_message_id=None)

    def test_no_match_when_both_headers_absent(self):
        expected = "<abc@example.com>"
        assert not message_id_matches(expected, message_id=None, x_google_original_message_id=None)

    def test_never_case_folds_local_part(self):
        expected = "<AbC123@example.com>"
        assert not message_id_matches(expected, message_id="<abc123@example.com>", x_google_original_message_id=None)
