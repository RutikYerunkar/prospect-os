"""V2-H, Critical Decision D2 — `resolve_send_provider(mode)`. Asserted in
BOTH directions: Demo -> `DemoEmailSendProvider`, never anything else; Live
-> always `LiveExternalEmailSendDisabled`, never a provider instance, and
never conditioned on any registered runtime/config. Also covers
`DemoEmailSendProvider.send()` itself: zero-egress, `ACCEPTED`,
`dispatched=True`, a `demo://`-prefixed message id.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from groundwork.models.enums import Mode
from groundwork.providers.send_base import (
    DemoEmailSendProvider,
    LiveExternalEmailSendDisabled,
    OutboundEmailMessage,
    SendOutcome,
)
from groundwork.providers.send_registry import resolve_send_provider


class TestResolveSendProviderModeBinding:
    def test_demo_resolves_to_demo_email_send_provider(self):
        provider = resolve_send_provider(Mode.DEMO)
        assert isinstance(provider, DemoEmailSendProvider)

    def test_live_always_raises_live_external_email_send_disabled(self):
        with pytest.raises(LiveExternalEmailSendDisabled):
            resolve_send_provider(Mode.LIVE)

    def test_live_refusal_message_names_missing_send_provider_not_the_resolved_suppression_prerequisite(self):
        """V2-I-a narrowed this message (docs/PROGRESS.md): the legal/privacy
        suppression prerequisite is now implemented, so the refusal must no
        longer claim it is unresolved. The refusal itself stays unconditional
        regardless — it now names the real remaining reason: no
        `GmailSendProvider` exists yet (V2-I-b)."""
        with pytest.raises(LiveExternalEmailSendDisabled) as exc_info:
            resolve_send_provider(Mode.LIVE)
        message = str(exc_info.value)
        assert "GmailSendProvider" in message
        assert "claimed_email" not in message

    def test_live_refusal_has_stable_code(self):
        with pytest.raises(LiveExternalEmailSendDisabled) as exc_info:
            resolve_send_provider(Mode.LIVE)
        assert exc_info.value.code == "LIVE_EXTERNAL_EMAIL_SEND_DISABLED"

    def test_live_refusal_is_never_provider_not_configured(self):
        """D1 — this must NOT be `ProviderNotConfigured`; registering a
        future send provider must not silently lift it."""
        from groundwork.providers.base import ProviderNotConfigured

        with pytest.raises(LiveExternalEmailSendDisabled) as exc_info:
            resolve_send_provider(Mode.LIVE)
        assert not isinstance(exc_info.value, ProviderNotConfigured)

    def test_live_refusal_unconditional_regardless_of_extra_arguments_shape(self):
        """The frozen brief's example signature is `resolve_send_provider(mode, ...)`
        — this implementation takes exactly `mode`; calling it with only
        `mode` for LIVE always raises, proving the refusal doesn't depend on
        any registered runtime/config that might otherwise be threaded in."""
        for _ in range(5):
            with pytest.raises(LiveExternalEmailSendDisabled):
                resolve_send_provider(Mode.LIVE)


class TestDemoEmailSendProviderSend:
    async def test_send_is_accepted_and_dispatched(self):
        provider = DemoEmailSendProvider()
        now = datetime.now(timezone.utc)
        msg = OutboundEmailMessage(
            to="priya.natarajan@northwindlabs.com",
            subject="Hi",
            body_text="Hi Priya",
            message_id_header=f"<test-{now.timestamp()}@groundwork.invalid>",
        )
        result = await provider.send(msg, idempotency_key="key-1")
        assert result.outcome is SendOutcome.ACCEPTED
        assert result.dispatched is True

    async def test_send_provider_message_id_starts_with_demo_scheme(self):
        provider = DemoEmailSendProvider()
        msg = OutboundEmailMessage(
            to="a@b.com", subject="Hi", body_text="Hi", message_id_header="<x@groundwork.invalid>"
        )
        result = await provider.send(msg, idempotency_key="key-2")
        assert result.provider_message_id is not None
        assert result.provider_message_id.startswith("demo://")

    async def test_send_is_deterministic_per_idempotency_key(self):
        provider = DemoEmailSendProvider()
        msg = OutboundEmailMessage(
            to="a@b.com", subject="Hi", body_text="Hi", message_id_header="<x@groundwork.invalid>"
        )
        r1 = await provider.send(msg, idempotency_key="same-key")
        r2 = await provider.send(msg, idempotency_key="same-key")
        assert r1.provider_message_id == r2.provider_message_id

    async def test_connected_account_identifier_is_synthetic(self):
        provider = DemoEmailSendProvider()
        identifier = await provider.connected_account_identifier()
        assert identifier == "demo-sender@groundwork.invalid"
        assert identifier.endswith("@groundwork.invalid")

    async def test_find_sent_message_not_called_in_v2h_raises_not_implemented(self):
        """Reconciliation is meaningless for a synchronous, always-settled
        Demo send — never called anywhere in V2-H; kept as a documented,
        deliberate `NotImplementedError` rather than a fake result."""
        provider = DemoEmailSendProvider()
        with pytest.raises(NotImplementedError):
            await provider.find_sent_message(
                pre_dispatch_history_id="1000",
                expected_subject="Hi",
                expected_recipient_identifier="prospect@example.com",
                expected_sender_identifier="demo-sender@groundwork.invalid",
                window_start=datetime.now(timezone.utc),
                window_end=datetime.now(timezone.utc),
                bounds=None,  # type: ignore[arg-type]
            )

    async def test_get_history_checkpoint_not_called_in_v2h_raises_not_implemented(self):
        """V2-I-b correction (post-smoke) — the pre-dispatch history
        checkpoint concept is meaningless for Demo's synchronous,
        always-settled send; Demo dispatch never goes through
        `dispatch_live_email_send` at all."""
        provider = DemoEmailSendProvider()
        with pytest.raises(NotImplementedError):
            await provider.get_history_checkpoint()
