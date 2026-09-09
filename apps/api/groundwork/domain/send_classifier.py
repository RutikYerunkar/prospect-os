"""The send failure taxonomy (§3.4, `docs/V2_IMPLEMENTATION_PLAN.md`) —
pure, no I/O, no provider import. `dispatched_at` is the classification
boundary: once the request body has been handed to the transport, a failure
to observe a definitive outcome is `ACCEPTANCE_UNKNOWN`, never a guess in
either direction.

This is an allow-list of PROVABLE outcomes, not a denylist — anything not
positively classified below lands in `ACCEPTANCE_UNKNOWN`. In particular, no
arbitrary Gmail error string/reason is ever load-bearing here: only which
phase the attempt reached, and (once a response was actually received) its
HTTP status code, decide the classification. Never resends.
"""

from __future__ import annotations

from enum import StrEnum

from groundwork.models.enums import ActionExecutionStatus, SendOutcome


class DispatchPhase(StrEnum):
    """What actually happened to one `messages.send` attempt, as observed by
    the caller (`providers/live/gmail_send.py`) — the only input this
    classifier needs beyond the HTTP status/parsed body, once known."""

    #: An exception before the request body was ever written to the
    #: transport — pre-flight validation failure, DNS failure, TCP connect
    #: refused/unreachable, TLS handshake failure, or a token-refresh
    #: failure on the *preceding* call. Provably never reached Gmail.
    PRE_DISPATCH_FAILURE = "PRE_DISPATCH_FAILURE"
    #: A response (of some kind) was received from Gmail.
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    #: The body was written, but no positively-classifiable response was
    #: ever received — a read timeout after dispatch, connection reset mid-
    #: response, or any client deadline firing after dispatch began.
    POST_DISPATCH_UNKNOWN = "POST_DISPATCH_UNKNOWN"


#: §3.4 — a parsed response whose semantics are unambiguously "not
#: accepted, and provably never will be." Deliberately does NOT include 429
#: (rate-limited is not the same claim as malformed/unauthorized/missing)
#: or 403 (insufficient-permission bodies vary too much across Gmail error
#: shapes to trust blindly — every 403 variant, regardless of body/reason,
#: stays `ACCEPTANCE_UNKNOWN`).
_DEFINITIVE_REJECTION_STATUSES = frozenset({400, 401, 404})


def classify_send_outcome(
    *,
    phase: DispatchPhase,
    http_status: int | None = None,
    message_id: str | None = None,
) -> SendOutcome:
    """The one classification function. `message_id` is Gmail's own
    `Message.id` from a parsed 200 body — its absence on an otherwise-200
    response is itself `ACCEPTANCE_UNKNOWN` (an unparseable/incomplete
    "success" is not proof of acceptance)."""
    if phase is DispatchPhase.PRE_DISPATCH_FAILURE:
        return SendOutcome.PROVEN_NOT_DISPATCHED
    if phase is DispatchPhase.POST_DISPATCH_UNKNOWN:
        return SendOutcome.ACCEPTANCE_UNKNOWN

    # RESPONSE_RECEIVED
    if http_status == 200:
        if message_id:
            return SendOutcome.ACCEPTED
        return SendOutcome.ACCEPTANCE_UNKNOWN  # 200 but unparseable / missing Message.id
    if http_status in _DEFINITIVE_REJECTION_STATUSES:
        return SendOutcome.DEFINITIVE_REJECTION
    # Every 403 variant, every 429, every 5xx, and any other/unknown status
    # all land here — never made load-bearing on the exact Gmail error text.
    return SendOutcome.ACCEPTANCE_UNKNOWN


#: §3.2 state-machine arrows: ACCEPTED -> SUCCEEDED; PROVEN_NOT_DISPATCHED
#: and DEFINITIVE_REJECTION both -> FAILED (the only state that frees a
#: recipient identity — §3.5B); ACCEPTANCE_UNKNOWN -> UNCERTAIN.
_OUTCOME_TO_STATUS: dict[SendOutcome, ActionExecutionStatus] = {
    SendOutcome.ACCEPTED: ActionExecutionStatus.SUCCEEDED,
    SendOutcome.PROVEN_NOT_DISPATCHED: ActionExecutionStatus.FAILED,
    SendOutcome.DEFINITIVE_REJECTION: ActionExecutionStatus.FAILED,
    SendOutcome.ACCEPTANCE_UNKNOWN: ActionExecutionStatus.UNCERTAIN,
}


def execution_status_for_outcome(outcome: SendOutcome) -> ActionExecutionStatus:
    return _OUTCOME_TO_STATUS[outcome]
