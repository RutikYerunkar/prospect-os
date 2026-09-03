"""The normative content/action hash (§3.9, rev 3 — restored and extended).

An approval binds channel + sender + recipient + subject + body through this
versioned hash. Any change to any of those fields voids the approval — but
only those fields: draft/proposal/approval/prospect/run ids, timestamps,
`claim_map`, `evidence_ids`, `policy_version`/`policy_snapshot`, provider
names, and every OAuth credential are deliberately excluded, so a cosmetic
re-run never gratuitously voids a valid approval.

Pure and deterministic: no `hash()`, no dict ordering dependence, no locale
dependence — stable across processes, platforms and Python versions.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata

from groundwork.domain.contact_identity import normalize_email_identity
from groundwork.models.enums import Channel

HASH_VERSION = "v1"


def _canonical_text(value: str | None) -> str | None:
    """NFC, LF line endings, no trailing whitespace per line, no leading/
    trailing blank lines."""
    if value is None:
        return None
    s = unicodedata.normalize("NFC", value)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(line.rstrip(" \t") for line in s.split("\n"))
    return s.strip("\n")


def _canonical_identifier(channel: Channel, value: str | None) -> str | None:
    if value is None:
        return None
    if channel is Channel.EMAIL:
        # The SAME function the recipient-level send policy uses (§3.5B) —
        # so identity, dedup and hashing can never disagree (§3.10).
        return normalize_email_identity(value)
    return _canonical_text(value)  # LINKEDIN: no casefold; slugs compared exactly


def content_hash(
    *,
    channel: Channel,
    sender_identifier: str | None,
    recipient_identifier: str | None,
    subject: str | None,
    body: str | None,
) -> str:
    """Included: exactly the authorization-relevant, transmitted surface —
    `hash_version`, `channel`, `sender_identifier`, `recipient_identifier`,
    `subject`, `body`. Nothing else."""
    payload = {
        "hash_version": HASH_VERSION,
        "channel": channel.value,
        "sender_identifier": _canonical_identifier(channel, sender_identifier),  # None for LINKEDIN
        "recipient_identifier": _canonical_identifier(channel, recipient_identifier),
        "subject": _canonical_text(subject),  # None for LINKEDIN
        "body": _canonical_text(body),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
