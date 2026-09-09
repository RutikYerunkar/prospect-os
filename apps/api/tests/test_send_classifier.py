"""§3.4 send failure taxonomy — `domain/send_classifier.py` (V2-I-b, Phase 2).
Pure function, exhaustively covering the taxonomy table from the frozen
plan/task brief."""

from __future__ import annotations

import pytest

from groundwork.domain.send_classifier import DispatchPhase, classify_send_outcome, execution_status_for_outcome
from groundwork.models.enums import ActionExecutionStatus, SendOutcome


def test_pre_dispatch_failure_is_proven_not_dispatched():
    outcome = classify_send_outcome(phase=DispatchPhase.PRE_DISPATCH_FAILURE)
    assert outcome is SendOutcome.PROVEN_NOT_DISPATCHED
    assert execution_status_for_outcome(outcome) is ActionExecutionStatus.FAILED


def test_post_dispatch_unknown_is_acceptance_unknown():
    outcome = classify_send_outcome(phase=DispatchPhase.POST_DISPATCH_UNKNOWN)
    assert outcome is SendOutcome.ACCEPTANCE_UNKNOWN
    assert execution_status_for_outcome(outcome) is ActionExecutionStatus.UNCERTAIN


def test_200_with_message_id_is_accepted():
    outcome = classify_send_outcome(phase=DispatchPhase.RESPONSE_RECEIVED, http_status=200, message_id="abc123")
    assert outcome is SendOutcome.ACCEPTED
    assert execution_status_for_outcome(outcome) is ActionExecutionStatus.SUCCEEDED


def test_200_without_message_id_is_acceptance_unknown():
    outcome = classify_send_outcome(phase=DispatchPhase.RESPONSE_RECEIVED, http_status=200, message_id=None)
    assert outcome is SendOutcome.ACCEPTANCE_UNKNOWN


def test_200_with_empty_message_id_is_acceptance_unknown():
    outcome = classify_send_outcome(phase=DispatchPhase.RESPONSE_RECEIVED, http_status=200, message_id="")
    assert outcome is SendOutcome.ACCEPTANCE_UNKNOWN


@pytest.mark.parametrize("status", [400, 401, 404])
def test_definitive_rejection_statuses(status):
    outcome = classify_send_outcome(phase=DispatchPhase.RESPONSE_RECEIVED, http_status=status)
    assert outcome is SendOutcome.DEFINITIVE_REJECTION
    assert execution_status_for_outcome(outcome) is ActionExecutionStatus.FAILED


@pytest.mark.parametrize("status", [403, 429, 500, 502, 503, 504])
def test_ambiguous_statuses_are_acceptance_unknown(status):
    outcome = classify_send_outcome(phase=DispatchPhase.RESPONSE_RECEIVED, http_status=status)
    assert outcome is SendOutcome.ACCEPTANCE_UNKNOWN


@pytest.mark.parametrize("status", [403, 429])
def test_every_403_and_429_variant_regardless_of_body_stays_unknown(status):
    # The classifier never even sees a body/reason — proving it structurally
    # cannot special-case one.
    outcome = classify_send_outcome(phase=DispatchPhase.RESPONSE_RECEIVED, http_status=status, message_id=None)
    assert outcome is SendOutcome.ACCEPTANCE_UNKNOWN


def test_unknown_status_is_acceptance_unknown():
    outcome = classify_send_outcome(phase=DispatchPhase.RESPONSE_RECEIVED, http_status=418)
    assert outcome is SendOutcome.ACCEPTANCE_UNKNOWN


def test_classifier_signature_never_takes_an_error_reason_string():
    """No arbitrary Gmail error string is ever load-bearing — enforced
    structurally: the function accepts no such parameter at all."""
    import inspect

    params = set(inspect.signature(classify_send_outcome).parameters)
    assert params == {"phase", "http_status", "message_id"}
