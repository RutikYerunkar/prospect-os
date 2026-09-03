"""§6.1 — the deterministic action policy. Every clause in isolation; no
override path exists; clause 12 blocks on UNCERTAIN and ABANDONED and
permits only FAILED (by the caller passing `RecipientConflict.NONE`);
clauses 10-11 block a changed sender; clause 9 supersedes on a
`hash_version` mismatch; clause 12 is skipped entirely for a
DEMO_SIMULATED proposal and never inspects DEMO_SIMULATED rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from groundwork.domain.action_policy import (
    POLICY_VERSION,
    RecipientConflict,
    evaluate,
)
from groundwork.domain.content_hash import HASH_VERSION, content_hash
from groundwork.models.enums import (
    ActionExecutionOrigin,
    ActionPolicyVerdict,
    ActionType,
    Channel,
    EmailDiscoveryState,
    EmailVerificationState,
    LinkedInIdentityState,
    LinkedInResolutionState,
    ProspectStatus,
    ReviewVerdict,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _valid_email_hash() -> str:
    return content_hash(
        channel=Channel.EMAIL,
        sender_identifier="demo-sender@groundwork.invalid",
        recipient_identifier="priya.natarajan@northwindlabs.com",
        subject="Hi",
        body="Hi Priya",
    )


def _base_email_kwargs(**overrides) -> dict:
    h = _valid_email_hash()
    base = dict(
        action_type=ActionType.EMAIL_SEND,
        origin=ActionExecutionOrigin.LIVE_EXTERNAL,
        review_verdict=ReviewVerdict.PASS,
        prospect_status=ProspectStatus.PASS,
        draft_channel=Channel.EMAIL,
        draft_subject="Hi",
        draft_body="Hi Priya",
        proposal_content_hash=h,
        recomputed_content_hash=h,
        proposal_hash_version=HASH_VERSION,
        approval_hash_version=HASH_VERSION,
        email_discovery_state=EmailDiscoveryState.FOUND,
        email_verification_state=EmailVerificationState.VERIFIED,
        email_observed_at=NOW,
        recipient_identifier="priya.natarajan@northwindlabs.com",
        connected_sender_identifier="demo-sender@groundwork.invalid",
        proposal_sender_identifier="demo-sender@groundwork.invalid",
        send_provider_configured=True,
        recipient_conflict=RecipientConflict.NONE,
        now=NOW,
    )
    base.update(overrides)
    return base


def _base_linkedin_kwargs(**overrides) -> dict:
    h = content_hash(
        channel=Channel.LINKEDIN,
        sender_identifier=None,
        recipient_identifier="demo://linkedin/priya-natarajan",
        subject=None,
        body="Hi Priya",
    )
    base = dict(
        action_type=ActionType.LINKEDIN_COPY_AND_OPEN,
        origin=ActionExecutionOrigin.DEMO_SIMULATED,
        review_verdict=ReviewVerdict.PASS,
        prospect_status=ProspectStatus.PASS,
        draft_channel=Channel.LINKEDIN,
        draft_subject=None,
        draft_body="Hi Priya",
        proposal_content_hash=h,
        recomputed_content_hash=h,
        proposal_hash_version=HASH_VERSION,
        approval_hash_version=HASH_VERSION,
        linkedin_discovery_state=LinkedInResolutionState.RESOLVED,
        linkedin_identity_match_state=LinkedInIdentityState.STRONG_MATCH,
        linkedin_observed_at=NOW,
        now=NOW,
    )
    base.update(overrides)
    return base


def test_policy_version_is_v1():
    assert POLICY_VERSION == "v1"


def test_fully_eligible_email_send():
    result = evaluate(**_base_email_kwargs())
    assert result.verdict is ActionPolicyVerdict.ELIGIBLE
    assert result.blocked_reasons == []


def test_fully_eligible_linkedin_copy_and_open():
    result = evaluate(**_base_linkedin_kwargs())
    assert result.verdict is ActionPolicyVerdict.ELIGIBLE
    assert result.blocked_reasons == []


class TestEmailSendClausesIndependently:
    def test_clause_1_review_not_passed(self):
        result = evaluate(**_base_email_kwargs(review_verdict=ReviewVerdict.NEEDS_REVIEW))
        assert "review_not_passed" in result.blocked_reasons
        assert result.verdict is ActionPolicyVerdict.BLOCKED

    @pytest.mark.parametrize(
        "status",
        [
            ProspectStatus.REJECTED,
            ProspectStatus.FAILED,
            ProspectStatus.DUPLICATE,
            ProspectStatus.TIMED_OUT,
            ProspectStatus.PENDING,
            ProspectStatus.RUNNING,
        ],
    )
    def test_clause_2_prospect_not_actionable(self, status):
        result = evaluate(**_base_email_kwargs(prospect_status=status))
        assert "prospect_not_actionable" in result.blocked_reasons

    @pytest.mark.parametrize(
        "state", [EmailDiscoveryState.NOT_ATTEMPTED, EmailDiscoveryState.NOT_FOUND, EmailDiscoveryState.PROVIDER_ERROR]
    )
    def test_clause_3_email_not_discovered(self, state):
        result = evaluate(**_base_email_kwargs(email_discovery_state=state))
        assert "email_not_discovered" in result.blocked_reasons

    @pytest.mark.parametrize(
        "state",
        [
            EmailVerificationState.UNVERIFIED,
            EmailVerificationState.UNVERIFIABLE,
            EmailVerificationState.RISKY,
            EmailVerificationState.INVALID,
        ],
    )
    def test_clause_4_email_not_verified_no_override(self, state):
        """VERIFIED is the ONLY sendable state — every other state blocks,
        with no override anywhere."""
        result = evaluate(**_base_email_kwargs(email_verification_state=state))
        assert "email_not_verified" in result.blocked_reasons

    def test_clause_5_contact_state_stale(self):
        stale = NOW - timedelta(days=31)
        result = evaluate(**_base_email_kwargs(email_observed_at=stale))
        assert "contact_state_stale" in result.blocked_reasons

    def test_clause_5_not_stale_within_window(self):
        fresh = NOW - timedelta(days=29)
        result = evaluate(**_base_email_kwargs(email_observed_at=fresh))
        assert "contact_state_stale" not in result.blocked_reasons

    def test_clause_5_missing_observed_at_is_stale(self):
        result = evaluate(**_base_email_kwargs(email_observed_at=None))
        assert "contact_state_stale" in result.blocked_reasons

    def test_clause_6_empty_subject(self):
        result = evaluate(**_base_email_kwargs(draft_subject=""))
        assert "draft_incomplete" in result.blocked_reasons

    def test_clause_6_empty_body(self):
        result = evaluate(**_base_email_kwargs(draft_body="   "))
        assert "draft_incomplete" in result.blocked_reasons

    def test_clause_6_wrong_draft_channel(self):
        result = evaluate(**_base_email_kwargs(draft_channel=Channel.LINKEDIN))
        assert "draft_incomplete" in result.blocked_reasons

    def test_clause_7_recipient_identity_invalid(self):
        result = evaluate(**_base_email_kwargs(recipient_identifier="not-an-email"))
        assert "recipient_identity_invalid" in result.blocked_reasons

    def test_clause_7_missing_recipient(self):
        result = evaluate(**_base_email_kwargs(recipient_identifier=None))
        assert "recipient_identity_invalid" in result.blocked_reasons

    def test_clause_8_content_changed(self):
        other_hash = content_hash(
            channel=Channel.EMAIL,
            sender_identifier="demo-sender@groundwork.invalid",
            recipient_identifier="priya.natarajan@northwindlabs.com",
            subject="A different subject",
            body="Hi Priya",
        )
        result = evaluate(**_base_email_kwargs(recomputed_content_hash=other_hash))
        assert "content_changed" in result.blocked_reasons

    def test_clause_9_approval_hash_version_mismatch_supersedes(self):
        result = evaluate(**_base_email_kwargs(approval_hash_version="v0"))
        assert "approval_superseded" in result.blocked_reasons

    def test_clause_9_proposal_hash_version_mismatch_supersedes(self):
        result = evaluate(**_base_email_kwargs(proposal_hash_version="v0"))
        assert "approval_superseded" in result.blocked_reasons

    def test_clause_9_missing_approval_hash_version_supersedes(self):
        result = evaluate(**_base_email_kwargs(approval_hash_version=None))
        assert "approval_superseded" in result.blocked_reasons

    def test_clause_10_sender_not_connected(self):
        result = evaluate(**_base_email_kwargs(connected_sender_identifier=None))
        assert "sender_not_connected" in result.blocked_reasons
        assert "sender_changed" not in result.blocked_reasons

    def test_clause_11_sender_changed(self):
        result = evaluate(**_base_email_kwargs(connected_sender_identifier="someone-else@groundwork.invalid"))
        assert "sender_changed" in result.blocked_reasons

    def test_clause_11_sender_changed_case_insensitive_match_not_flagged(self):
        # Both canonical via normalize_email_identity — a casing-only
        # difference is NOT a sender change.
        result = evaluate(**_base_email_kwargs(connected_sender_identifier="Demo-Sender@Groundwork.INVALID"))
        assert "sender_changed" not in result.blocked_reasons

    def test_clause_11_malformed_connected_sender_blocks(self):
        result = evaluate(**_base_email_kwargs(connected_sender_identifier="not-an-email"))
        assert "sender_changed" in result.blocked_reasons

    def test_clause_13_send_provider_unavailable(self):
        result = evaluate(**_base_email_kwargs(send_provider_configured=False))
        assert "send_provider_unavailable" in result.blocked_reasons

    def test_clause_14_live_allowance_exhausted(self):
        result = evaluate(**_base_email_kwargs(origin=ActionExecutionOrigin.LIVE_EXTERNAL, live_allowance_exhausted=True))
        assert "send_allowance_exhausted" in result.blocked_reasons

    def test_clause_14_demo_cap_reached(self):
        result = evaluate(
            **_base_email_kwargs(
                origin=ActionExecutionOrigin.DEMO_SIMULATED,
                connected_sender_identifier="demo-sender@groundwork.invalid",
                proposal_sender_identifier="demo-sender@groundwork.invalid",
                demo_action_cap_reached=True,
            )
        )
        assert "demo_action_cap_reached" in result.blocked_reasons
        assert "send_allowance_exhausted" not in result.blocked_reasons


class TestNoOverridePath:
    def test_result_has_no_override_field(self):
        result = evaluate(**_base_email_kwargs(email_verification_state=EmailVerificationState.RISKY))
        assert not hasattr(result, "override")
        assert not hasattr(result, "override_reasons")
        assert result.verdict is ActionPolicyVerdict.BLOCKED

    def test_evaluate_accepts_no_override_kwarg(self):
        # A deliberate structural proof, not just a docstring claim: passing
        # an override-shaped kwarg is a TypeError, because no such
        # parameter exists on the function signature.
        with pytest.raises(TypeError):
            evaluate(**_base_email_kwargs(), override=True)  # type: ignore[call-arg]


class TestClause12RecipientConflictLiveOnly:
    @pytest.mark.parametrize(
        "conflict,reason",
        [
            (RecipientConflict.CLAIMED, "send_in_flight"),
            (RecipientConflict.IN_FLIGHT, "send_in_flight"),
            (RecipientConflict.SUCCEEDED, "already_sent_to_recipient"),
            (RecipientConflict.UNCERTAIN, "prior_send_uncertain"),
            (RecipientConflict.ABANDONED, "already_sent_to_recipient"),
        ],
    )
    def test_live_conflict_blocks_with_correct_reason(self, conflict, reason):
        result = evaluate(**_base_email_kwargs(origin=ActionExecutionOrigin.LIVE_EXTERNAL, recipient_conflict=conflict))
        assert reason in result.blocked_reasons

    def test_live_no_conflict_is_not_blocked_by_clause_12(self):
        result = evaluate(**_base_email_kwargs(origin=ActionExecutionOrigin.LIVE_EXTERNAL, recipient_conflict=RecipientConflict.NONE))
        assert "already_sent_to_recipient" not in result.blocked_reasons
        assert "prior_send_uncertain" not in result.blocked_reasons
        assert "send_in_flight" not in result.blocked_reasons

    def test_failed_only_never_blocks(self):
        """FAILED is not a RecipientConflict member at all — a caller who
        only found FAILED rows for this recipient passes NONE, and FAILED
        is the only state that frees a recipient identity (§3.5B)."""
        assert not hasattr(RecipientConflict, "FAILED")
        result = evaluate(**_base_email_kwargs(origin=ActionExecutionOrigin.LIVE_EXTERNAL, recipient_conflict=RecipientConflict.NONE))
        assert result.verdict is ActionPolicyVerdict.ELIGIBLE

    @pytest.mark.parametrize(
        "conflict",
        [
            RecipientConflict.CLAIMED,
            RecipientConflict.IN_FLIGHT,
            RecipientConflict.SUCCEEDED,
            RecipientConflict.UNCERTAIN,
            RecipientConflict.ABANDONED,
        ],
    )
    def test_demo_simulated_never_blocked_by_clause_12_even_if_conflict_passed(self, conflict):
        """Clause 12 is the only clause whose applicability depends on
        origin, and it is symmetrically isolated: a demo proposal is never
        blocked by anything, and never blocks anything — even if a caller
        erroneously computed a conflict for it."""
        result = evaluate(
            **_base_email_kwargs(
                origin=ActionExecutionOrigin.DEMO_SIMULATED,
                connected_sender_identifier="demo-sender@groundwork.invalid",
                proposal_sender_identifier="demo-sender@groundwork.invalid",
                recipient_conflict=conflict,
            )
        )
        assert "already_sent_to_recipient" not in result.blocked_reasons
        assert "prior_send_uncertain" not in result.blocked_reasons
        assert "send_in_flight" not in result.blocked_reasons
        assert result.verdict is ActionPolicyVerdict.ELIGIBLE


class TestLinkedInClauses:
    def test_clause_1_and_2_apply(self):
        result = evaluate(**_base_linkedin_kwargs(review_verdict=ReviewVerdict.FAIL))
        assert "review_not_passed" in result.blocked_reasons

    def test_clause_5_analogue_staleness(self):
        stale = NOW - timedelta(days=31)
        result = evaluate(**_base_linkedin_kwargs(linkedin_observed_at=stale))
        assert "contact_state_stale" in result.blocked_reasons

    def test_clause_6_body_only_empty_body_blocks(self):
        result = evaluate(**_base_linkedin_kwargs(draft_body=""))
        assert "draft_incomplete" in result.blocked_reasons

    def test_clause_6_subject_irrelevant_for_linkedin(self):
        # LinkedIn has no subject at all — a None subject must never itself
        # trigger draft_incomplete.
        result = evaluate(**_base_linkedin_kwargs(draft_subject=None))
        assert "draft_incomplete" not in result.blocked_reasons

    def test_linkedin_not_resolved_blocks(self):
        result = evaluate(**_base_linkedin_kwargs(linkedin_discovery_state=LinkedInResolutionState.NOT_FOUND))
        assert "linkedin_not_resolved" in result.blocked_reasons

    @pytest.mark.parametrize(
        "state", [LinkedInIdentityState.UNKNOWN, LinkedInIdentityState.WEAK_MATCH, LinkedInIdentityState.MISMATCH]
    )
    def test_linkedin_identity_not_strong_blocks(self, state):
        result = evaluate(**_base_linkedin_kwargs(linkedin_identity_match_state=state))
        assert "linkedin_identity_not_strong" in result.blocked_reasons

    def test_clause_8_and_9_apply(self):
        result = evaluate(**_base_linkedin_kwargs(proposal_hash_version="v0"))
        assert "approval_superseded" in result.blocked_reasons

    def test_email_only_clauses_never_appear_for_linkedin(self):
        result = evaluate(**_base_linkedin_kwargs())
        for reason in (
            "email_not_discovered",
            "email_not_verified",
            "recipient_identity_invalid",
            "sender_not_connected",
            "sender_changed",
            "already_sent_to_recipient",
            "prior_send_uncertain",
            "send_in_flight",
            "send_provider_unavailable",
            "send_allowance_exhausted",
            "demo_action_cap_reached",
        ):
            assert reason not in result.blocked_reasons


class TestPolicySnapshot:
    def test_snapshot_carries_policy_version_and_action_type(self):
        result = evaluate(**_base_email_kwargs())
        assert result.policy_snapshot["policy_version"] == POLICY_VERSION
        assert result.policy_snapshot["action_type"] == ActionType.EMAIL_SEND.value
        assert result.policy_version == POLICY_VERSION
