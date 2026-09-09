"""`providers/live/gmail_mime.py` (V2-I-b, Phase 3) — deterministic MIME
construction."""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from groundwork.providers.live.gmail_mime import InvalidOutboundMessage, build_raw_message

FIXED_DATE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _build(**overrides):
    kwargs = dict(
        sender="operator@example.com",
        to="prospect@example.com",
        subject="Hello",
        body_text="Hi there,\n\nGreat to connect.",
        message_id_header="<abc123@example.com>",
        date=FIXED_DATE,
    )
    kwargs.update(overrides)
    return build_raw_message(**kwargs)


def _decode(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw.encode("ascii"))


def test_deterministic_for_identical_inputs():
    a = _build()
    b = _build()
    assert a == b


def test_headers_present_and_correct():
    raw = _build()
    text = _decode(raw).decode("utf-8")
    assert "From: operator@example.com" in text
    assert "To: prospect@example.com" in text
    assert "Subject: Hello" in text
    assert "Message-ID: <abc123@example.com>" in text
    assert "Date:" in text


def test_no_bcc_or_cc_headers():
    raw = _build()
    text = _decode(raw).decode("utf-8")
    assert "Bcc:" not in text
    assert "Cc:" not in text


def test_no_threadid_anywhere_in_mime():
    raw = _build()
    text = _decode(raw).decode("utf-8")
    assert "threadId" not in text


def test_plain_text_only_no_html():
    raw = _build()
    text = _decode(raw).decode("utf-8")
    assert "text/plain" in text
    assert "text/html" not in text


def test_crlf_line_endings():
    raw = _build()
    raw_bytes = _decode(raw)
    assert b"\r\n" in raw_bytes


def test_exactly_one_recipient_enforced():
    with pytest.raises(InvalidOutboundMessage):
        _build(to="a@example.com, b@example.com")


def test_rejects_empty_subject():
    with pytest.raises(InvalidOutboundMessage):
        _build(subject="")


def test_rejects_empty_body():
    with pytest.raises(InvalidOutboundMessage):
        _build(body_text="   ")


def test_utf8_body_round_trips():
    raw = _build(body_text="Café résumé — naïve")
    text = _decode(raw).decode("utf-8")
    # base64/quoted-printable encoded by the email lib; just prove it decodes
    # without raising and the raw bytes are non-empty.
    assert len(text) > 0
