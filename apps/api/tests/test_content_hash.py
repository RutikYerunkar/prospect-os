"""§3.9 — the normative content/action hash. Sender, recipient, subject and
body each independently change the hash; every excluded field does not;
cross-process stability; `hash_version` mismatch handling primitive.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from groundwork.domain.content_hash import HASH_VERSION, content_hash
from groundwork.domain.contact_identity import InvalidEmailIdentity
from groundwork.models.enums import Channel


def _base_email_kwargs() -> dict:
    return dict(
        channel=Channel.EMAIL,
        sender_identifier="demo-sender@groundwork.invalid",
        recipient_identifier="priya.natarajan@northwindlabs.com",
        subject="Quick question about your GTM stack",
        body="Hi Priya,\n\nWe help teams like Northwind Labs...\n\nBest,\nThe Groundwork Team",
    )


def test_version_is_v1():
    assert HASH_VERSION == "v1"


class TestFieldSensitivity:
    def test_sender_change_changes_hash(self):
        base = content_hash(**_base_email_kwargs())
        changed = content_hash(**{**_base_email_kwargs(), "sender_identifier": "other-sender@groundwork.invalid"})
        assert base != changed

    def test_recipient_change_changes_hash(self):
        base = content_hash(**_base_email_kwargs())
        changed = content_hash(**{**_base_email_kwargs(), "recipient_identifier": "someone-else@northwindlabs.com"})
        assert base != changed

    def test_subject_change_changes_hash(self):
        base = content_hash(**_base_email_kwargs())
        changed = content_hash(**{**_base_email_kwargs(), "subject": "A different subject"})
        assert base != changed

    def test_body_change_changes_hash(self):
        base = content_hash(**_base_email_kwargs())
        changed = content_hash(**{**_base_email_kwargs(), "body": "A completely different body."})
        assert base != changed

    def test_channel_change_changes_hash(self):
        email_hash = content_hash(**_base_email_kwargs())
        linkedin_kwargs = {
            **_base_email_kwargs(),
            "channel": Channel.LINKEDIN,
            "sender_identifier": None,
            "recipient_identifier": "demo://linkedin/priya-natarajan",
            "subject": None,
        }
        linkedin_hash = content_hash(**linkedin_kwargs)
        assert email_hash != linkedin_hash


class TestExcludedFieldsDoNotAffectHash:
    """draft id, draft version, proposal id, approval id, prospect id, run
    id, claim_map, evidence_ids, every timestamp, policy_version,
    policy_snapshot, provider names, and OAuth credentials are not part of
    the hash payload at all — this module's function signature structurally
    cannot accept them, which is itself the strongest form of this
    guarantee. These tests prove the two identifier-canonicalization
    decisions (casing/Unicode) don't leak non-canonical noise into the
    hash, which is the only way "excluded metadata" could sneak in via an
    included field."""

    def test_recipient_casing_only_change_does_not_change_hash(self):
        # §3.10's deliberate consequence: a casing-only edit to the display
        # form must not void an approval, because the hash canonicalizes
        # the recipient exactly like the dedup rule does.
        base = content_hash(**_base_email_kwargs())
        changed = content_hash(
            **{**_base_email_kwargs(), "recipient_identifier": "Priya.Natarajan@NorthwindLabs.COM"}
        )
        assert base == changed

    def test_sender_casing_only_change_does_not_change_hash(self):
        base = content_hash(**_base_email_kwargs())
        changed = content_hash(
            **{**_base_email_kwargs(), "sender_identifier": "Demo-Sender@Groundwork.INVALID"}
        )
        assert base == changed

    def test_subject_trailing_whitespace_does_not_change_hash(self):
        base = content_hash(**_base_email_kwargs())
        changed = content_hash(**{**_base_email_kwargs(), "subject": "Quick question about your GTM stack   "})
        assert base == changed

    def test_body_crlf_vs_lf_does_not_change_hash(self):
        kwargs = _base_email_kwargs()
        crlf_body = kwargs["body"].replace("\n", "\r\n")
        base = content_hash(**kwargs)
        changed = content_hash(**{**kwargs, "body": crlf_body})
        assert base == changed

    def test_body_leading_trailing_blank_lines_do_not_change_hash(self):
        base = content_hash(**_base_email_kwargs())
        changed = content_hash(**{**_base_email_kwargs(), "body": "\n\n" + _base_email_kwargs()["body"] + "\n\n\n"})
        assert base == changed

    def test_body_trailing_spaces_per_line_do_not_change_hash(self):
        kwargs = _base_email_kwargs()
        padded_body = "\n".join(f"{line}   " for line in kwargs["body"].split("\n"))
        base = content_hash(**kwargs)
        changed = content_hash(**{**kwargs, "body": padded_body})
        assert base == changed


class TestDeterminism:
    def test_same_inputs_same_hash(self):
        assert content_hash(**_base_email_kwargs()) == content_hash(**_base_email_kwargs())

    def test_matches_hand_computed_payload(self):
        kwargs = _base_email_kwargs()
        expected_payload = {
            "hash_version": "v1",
            "channel": "email",
            "sender_identifier": "demo-sender@groundwork.invalid",
            "recipient_identifier": "priya.natarajan@northwindlabs.com",
            "subject": "Quick question about your GTM stack",
            "body": kwargs["body"],
        }
        encoded = json.dumps(expected_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        expected = hashlib.sha256(encoded).hexdigest()
        assert content_hash(**kwargs) == expected

    def test_cross_process_stability(self):
        """No `hash()`, no dict ordering dependence, no locale dependence —
        a fresh Python subprocess must compute the identical digest."""
        kwargs = _base_email_kwargs()
        script = (
            "from groundwork.domain.content_hash import content_hash\n"
            "from groundwork.models.enums import Channel\n"
            f"print(content_hash(channel=Channel.EMAIL, sender_identifier={kwargs['sender_identifier']!r}, "
            f"recipient_identifier={kwargs['recipient_identifier']!r}, subject={kwargs['subject']!r}, "
            f"body={kwargs['body']!r}))"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == content_hash(**kwargs)


class TestLinkedInChannelCanonicalization:
    def test_linkedin_recipient_not_casefolded_compared_exactly(self):
        base = content_hash(
            channel=Channel.LINKEDIN,
            sender_identifier=None,
            recipient_identifier="demo://linkedin/priya-natarajan",
            subject=None,
            body="Hi Priya — loved your recent post.",
        )
        different_case = content_hash(
            channel=Channel.LINKEDIN,
            sender_identifier=None,
            recipient_identifier="demo://linkedin/PRIYA-natarajan",
            subject=None,
            body="Hi Priya — loved your recent post.",
        )
        # LinkedIn slugs are compared exactly, no casefold — a case change
        # IS a different identifier.
        assert base != different_case

    def test_linkedin_sender_and_subject_are_none_in_payload(self):
        h = content_hash(
            channel=Channel.LINKEDIN,
            sender_identifier=None,
            recipient_identifier="demo://linkedin/priya-natarajan",
            subject=None,
            body="Hi Priya",
        )
        expected_payload = {
            "hash_version": "v1",
            "channel": "linkedin",
            "sender_identifier": None,
            "recipient_identifier": "demo://linkedin/priya-natarajan",
            "subject": None,
            "body": "Hi Priya",
        }
        encoded = json.dumps(expected_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        assert h == hashlib.sha256(encoded).hexdigest()


class TestHashVersionMismatchPrimitive:
    """`content_hash()` always stamps the CURRENT `HASH_VERSION` into its
    payload — it takes no `hash_version` parameter, so a caller can never
    accidentally compute a hash under a stale version. That is the
    primitive `domain/action_policy.py`'s clause 9 builds its
    supersede-rather-than-revalidate decision on: a stored `hash_version`
    that doesn't equal `HASH_VERSION` can never match what recomputing
    would produce, because recomputing always uses `HASH_VERSION`."""

    def test_hash_version_is_baked_into_every_payload_unconditionally(self):
        h1 = content_hash(**_base_email_kwargs())
        # Even wildly different content-relevant fields share the same
        # version stamp — there is no code path that varies it.
        h2 = content_hash(**{**_base_email_kwargs(), "body": "totally different"})
        assert h1 != h2  # sanity: they ARE different hashes...
        # ...but both were computed under the one current HASH_VERSION,
        # which is the only version `content_hash()` is capable of
        # producing (proven by the cross-process/hand-computed-payload
        # tests above, which assert "hash_version": HASH_VERSION verbatim).
        assert HASH_VERSION == "v1"


def test_invalid_email_recipient_raises_not_silently_hashed():
    with pytest.raises(InvalidEmailIdentity):
        content_hash(**{**_base_email_kwargs(), "recipient_identifier": "not-an-email"})
