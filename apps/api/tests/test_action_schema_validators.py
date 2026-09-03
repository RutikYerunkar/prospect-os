"""Part 5/§H model validators 3-4 — the `Evidence._no_fake_sources`
discipline extended to the action-execution/action-proposal demo
conventions (D9): a `DEMO_SIMULATED` row is structurally incapable of
carrying a real-looking provider message id or a non-`@groundwork.invalid`
sender.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from groundwork.models.enums import ActionExecutionOrigin
from groundwork.models.schemas import DEMO_SENDER_DOMAIN, ActionExecution, ActionProposal

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _proposal(**overrides) -> dict:
    base = dict(
        prospect_id="p1",
        run_id="r1",
        draft_id="d1",
        action_type="EMAIL_SEND",
        channel="email",
        content_hash="deadbeef",
        hash_version="v1",
        policy_version="v1",
        policy_verdict="ELIGIBLE",
        origin=ActionExecutionOrigin.DEMO_SIMULATED,
        created_at=NOW,
    )
    base.update(overrides)
    return base


def _execution(**overrides) -> dict:
    base = dict(
        action_proposal_id="ap1",
        prospect_id="p1",
        run_id="r1",
        action_type="EMAIL_SEND",
        status="CLAIMED",
        idempotency_key="idem1",
        origin=ActionExecutionOrigin.DEMO_SIMULATED,
    )
    base.update(overrides)
    return base


class TestDemoSenderConvention:
    def test_demo_sender_domain_constant(self):
        assert DEMO_SENDER_DOMAIN == "groundwork.invalid"

    def test_valid_demo_sender_persists(self):
        row = ActionProposal(**_proposal(sender_identifier="demo-sender@groundwork.invalid"))
        assert row.sender_identifier == "demo-sender@groundwork.invalid"

    def test_non_groundwork_invalid_sender_rejected(self):
        with pytest.raises(ValueError):
            ActionProposal(**_proposal(sender_identifier="demo-sender@gmail.com"))

    def test_real_looking_sender_rejected(self):
        with pytest.raises(ValueError):
            ActionProposal(**_proposal(sender_identifier="priya.natarajan@northwindlabs.com"))

    def test_none_sender_persists_for_linkedin_proposals(self):
        # sender_identifier is NULL for LINKEDIN — the validator must not
        # reject the absence of a sender.
        row = ActionProposal(**_proposal(action_type="LINKEDIN_COPY_AND_OPEN", channel="linkedin", sender_identifier=None))
        assert row.sender_identifier is None

    def test_live_external_proposal_not_bound_by_demo_convention(self):
        row = ActionProposal(
            **_proposal(origin=ActionExecutionOrigin.LIVE_EXTERNAL, sender_identifier="real.sender@company.com")
        )
        assert row.sender_identifier == "real.sender@company.com"


class TestDemoProviderMessageIdConvention:
    def test_valid_demo_message_id_persists(self):
        row = ActionExecution(**_execution(provider_message_id="demo://gmail/message/1"))
        assert row.provider_message_id == "demo://gmail/message/1"

    def test_real_looking_message_id_rejected(self):
        with pytest.raises(ValueError):
            ActionExecution(**_execution(provider_message_id="18c9a1f2e3d4b5a6"))

    def test_none_message_id_persists(self):
        row = ActionExecution(**_execution(provider_message_id=None))
        assert row.provider_message_id is None

    def test_live_external_execution_not_bound_by_demo_convention(self):
        row = ActionExecution(
            **_execution(origin=ActionExecutionOrigin.LIVE_EXTERNAL, provider_message_id="18c9a1f2e3d4b5a6")
        )
        assert row.provider_message_id == "18c9a1f2e3d4b5a6"
