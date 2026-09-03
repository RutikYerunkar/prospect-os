"""The deterministic action policy (§6.1, `domain/action_policy.py` — pure).

Returns `(verdict, blocked_reasons, policy_snapshot)`, exactly like
`domain/review.py::run_checks` returns a verdict from typed inputs — no
database access, no provider call, no LLM anywhere in this path. No clause
has an override (D7): a BLOCKED verdict carries its reasons and offers no
button.

Every clause binds identically in Demo and Live EXCEPT clause 12 (the
recipient-level duplicate-send rule), which is `LIVE_EXTERNAL`-only and is
skipped entirely — never even evaluated — for a `DEMO_SIMULATED` proposal.
That is deliberate: a Demo walkthrough must exercise the *real* policy, not
a relaxed one.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from groundwork.domain.content_hash import HASH_VERSION
from groundwork.domain.contact_identity import InvalidEmailIdentity, normalize_email_identity
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
from groundwork.timeutil import ensure_aware, utcnow

POLICY_VERSION = "v1"

ENRICHMENT_STALE_AFTER_DAYS_DEFAULT = 30

#: §6.1 clause 2 — a prospect in any of these statuses is not actionable.
_NON_ACTIONABLE_PROSPECT_STATUSES = frozenset(
    {
        ProspectStatus.REJECTED,
        ProspectStatus.FAILED,
        ProspectStatus.DUPLICATE,
        ProspectStatus.TIMED_OUT,
        ProspectStatus.PENDING,
        ProspectStatus.RUNNING,
    }
)


class RecipientConflict(StrEnum):
    """What the caller found (from `action_executions`, LIVE_EXTERNAL rows
    only) for this recipient identity before calling `evaluate()` — this
    module has no database access, so the caller is responsible for
    computing it from §3.5B's partial-unique-index-protected state.
    `FAILED` is deliberately not a member: §3.4 defines `FAILED` as
    *provably* non-delivered, and it is the only state that frees a
    recipient identity, so a caller that only found `FAILED` rows passes
    `NONE`."""

    NONE = "NONE"
    CLAIMED = "CLAIMED"
    IN_FLIGHT = "IN_FLIGHT"
    SUCCEEDED = "SUCCEEDED"
    UNCERTAIN = "UNCERTAIN"
    ABANDONED = "ABANDONED"


#: Maps each blocking `RecipientConflict` to its clause-12 blocked reason.
_RECIPIENT_CONFLICT_REASONS: dict[RecipientConflict, str] = {
    RecipientConflict.CLAIMED: "send_in_flight",
    RecipientConflict.IN_FLIGHT: "send_in_flight",
    RecipientConflict.SUCCEEDED: "already_sent_to_recipient",
    RecipientConflict.ABANDONED: "already_sent_to_recipient",
    RecipientConflict.UNCERTAIN: "prior_send_uncertain",
}


class ActionPolicyResult(BaseModel):
    verdict: ActionPolicyVerdict
    blocked_reasons: list[str] = Field(default_factory=list)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    policy_version: str = POLICY_VERSION


def _is_stale(observed_at: datetime | None, now: datetime, stale_after_days: int) -> bool:
    if observed_at is None:
        return True
    observed_aware = ensure_aware(observed_at)
    assert observed_aware is not None
    return (now - observed_aware).total_seconds() > stale_after_days * 86400


def _recipient_normalizes(recipient_identifier: str | None) -> bool:
    if not recipient_identifier:
        return False
    try:
        normalize_email_identity(recipient_identifier)
    except InvalidEmailIdentity:
        return False
    return True


def evaluate(
    *,
    action_type: ActionType,
    origin: ActionExecutionOrigin,
    review_verdict: ReviewVerdict,
    prospect_status: ProspectStatus,
    draft_channel: Channel,
    draft_subject: str | None,
    draft_body: str | None,
    proposal_content_hash: str,
    recomputed_content_hash: str,
    proposal_hash_version: str,
    approval_hash_version: str | None,
    email_discovery_state: EmailDiscoveryState | None = None,
    email_verification_state: EmailVerificationState | None = None,
    email_observed_at: datetime | None = None,
    linkedin_discovery_state: LinkedInResolutionState | None = None,
    linkedin_identity_match_state: LinkedInIdentityState | None = None,
    linkedin_observed_at: datetime | None = None,
    recipient_identifier: str | None = None,
    connected_sender_identifier: str | None = None,
    proposal_sender_identifier: str | None = None,
    send_provider_configured: bool = False,
    recipient_conflict: RecipientConflict = RecipientConflict.NONE,
    live_allowance_exhausted: bool = False,
    demo_action_cap_reached: bool = False,
    now: datetime | None = None,
    stale_after_days: int = ENRICHMENT_STALE_AFTER_DAYS_DEFAULT,
) -> ActionPolicyResult:
    now = now or utcnow()
    reasons: list[str] = []

    # --- clause 1 — review_not_passed (both action types; PASS hard floor) ---
    if review_verdict is not ReviewVerdict.PASS:
        reasons.append("review_not_passed")

    # --- clause 2 — prospect_not_actionable (both) ---
    if prospect_status in _NON_ACTIONABLE_PROSPECT_STATUSES:
        reasons.append("prospect_not_actionable")

    if action_type is ActionType.EMAIL_SEND:
        # --- clause 3 ---
        if email_discovery_state is not EmailDiscoveryState.FOUND:
            reasons.append("email_not_discovered")
        # --- clause 4 — VERIFIED is the only sendable state, no override ---
        if email_verification_state is not EmailVerificationState.VERIFIED:
            reasons.append("email_not_verified")
        # --- clause 5 ---
        if _is_stale(email_observed_at, now, stale_after_days):
            reasons.append("contact_state_stale")
        # --- clause 6 — draft channel is EMAIL; subject and body both non-empty ---
        if (
            draft_channel is not Channel.EMAIL
            or not (draft_subject and draft_subject.strip())
            or not (draft_body and draft_body.strip())
        ):
            reasons.append("draft_incomplete")
        # --- clause 7 ---
        if not _recipient_normalizes(recipient_identifier):
            reasons.append("recipient_identity_invalid")
    else:  # LINKEDIN_COPY_AND_OPEN — clauses 3-4, 7, 10-14 do not apply
        # --- clause 5 analogue — the LinkedIn channel's own staleness ---
        if _is_stale(linkedin_observed_at, now, stale_after_days):
            reasons.append("contact_state_stale")
        # --- clause 6 (body only — a LinkedIn message has no subject) ---
        if not (draft_body and draft_body.strip()):
            reasons.append("draft_incomplete")
        # --- LinkedIn-specific eligibility ---
        if linkedin_discovery_state is not LinkedInResolutionState.RESOLVED:
            reasons.append("linkedin_not_resolved")
        if linkedin_identity_match_state is not LinkedInIdentityState.STRONG_MATCH:
            reasons.append("linkedin_identity_not_strong")

    # --- clause 8 — content_changed (both); constant-time per §3.9 step 6 ---
    if not hmac.compare_digest(proposal_content_hash, recomputed_content_hash):
        reasons.append("content_changed")

    # --- clause 9 — approval_superseded (both; rev 4) ---
    if (
        proposal_hash_version != HASH_VERSION
        or approval_hash_version != HASH_VERSION
        or approval_hash_version != proposal_hash_version
    ):
        reasons.append("approval_superseded")

    if action_type is ActionType.EMAIL_SEND:
        # --- clauses 10-11 — sender identity, re-verified fresh ---
        if not connected_sender_identifier:
            reasons.append("sender_not_connected")
        else:
            try:
                normalized_connected = normalize_email_identity(connected_sender_identifier)
            except InvalidEmailIdentity:
                normalized_connected = None
            if (
                normalized_connected is None
                or not proposal_sender_identifier
                or not hmac.compare_digest(normalized_connected, proposal_sender_identifier)
            ):
                reasons.append("sender_changed")

        # --- clause 12 — LIVE ONLY (rev 4); never evaluated for DEMO_SIMULATED ---
        if origin is ActionExecutionOrigin.LIVE_EXTERNAL and recipient_conflict is not RecipientConflict.NONE:
            reasons.append(_RECIPIENT_CONFLICT_REASONS[recipient_conflict])

        # --- clause 13 ---
        if not send_provider_configured:
            reasons.append("send_provider_unavailable")

        # --- clause 14 — allowance, origin-scoped ---
        if origin is ActionExecutionOrigin.LIVE_EXTERNAL:
            if live_allowance_exhausted:
                reasons.append("send_allowance_exhausted")
        elif demo_action_cap_reached:
            reasons.append("demo_action_cap_reached")

    verdict = ActionPolicyVerdict.BLOCKED if reasons else ActionPolicyVerdict.ELIGIBLE
    snapshot = {
        "policy_version": POLICY_VERSION,
        "action_type": action_type.value,
        "origin": origin.value,
        "evaluated_at": now.isoformat(),
    }
    return ActionPolicyResult(
        verdict=verdict,
        blocked_reasons=reasons,
        policy_snapshot=snapshot,
        policy_version=POLICY_VERSION,
    )
