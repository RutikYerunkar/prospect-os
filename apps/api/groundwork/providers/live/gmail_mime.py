"""Deterministic RFC 5322 MIME construction for one outbound Gmail message
(V2-I-b, Phase 3). Pure — no I/O, no clock reads. Every timestamp the
message needs (`Date`) is passed in by the caller; this module never calls
`datetime.now()`/`utcnow()` itself, so the same inputs always produce the
same `raw` bytes (verified by a determinism unit test).

Deliberately minimal: plain text UTF-8 only, no HTML, no attachments, no
`Bcc`/`Cc`, exactly one `To`, and no `threadId` (that is a top-level Gmail
API `messages.send` request field, never a MIME header — this module never
touches it). `From` is always the connected account's own identity, passed
in by the caller — never resolved or defaulted here.
"""

from __future__ import annotations

import base64
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime


class InvalidOutboundMessage(ValueError):
    """A structural precondition (exactly one recipient, non-empty fields)
    was violated — fails closed rather than constructing a malformed or
    ambiguous message."""


def _require_single_recipient(to: str) -> str:
    to = to.strip()
    if not to or "," in to or "\n" in to or "\r" in to:
        raise InvalidOutboundMessage(f"exactly one recipient address is required, got {to!r}")
    return to


def build_raw_message(
    *,
    sender: str,
    to: str,
    subject: str,
    body_text: str,
    message_id_header: str,
    date: datetime,
) -> str:
    """Builds the RFC 5322 message, serializes it with CRLF line endings
    (`email.policy.SMTP`), and returns the base64url-encoded `raw` string
    Gmail's `messages.send` expects. Deterministic for identical inputs —
    plain-text single-part messages need no MIME boundary, so nothing here
    is randomly generated."""
    if not sender.strip():
        raise InvalidOutboundMessage("sender must be non-empty")
    if not subject.strip():
        raise InvalidOutboundMessage("subject must be non-empty")
    if not body_text.strip():
        raise InvalidOutboundMessage("body_text must be non-empty")
    if not message_id_header.strip():
        raise InvalidOutboundMessage("message_id_header must be non-empty")
    recipient = _require_single_recipient(to)

    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Date"] = format_datetime(date)
    msg["Message-ID"] = message_id_header
    msg.set_content(body_text, subtype="plain", charset="utf-8")

    raw_bytes = msg.as_bytes()
    return base64.urlsafe_b64encode(raw_bytes).decode("ascii")
