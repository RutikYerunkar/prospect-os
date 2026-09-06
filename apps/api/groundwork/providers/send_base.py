"""`EmailSendProvider` — the provider-neutral sending-identity contract
(docs/V2_IMPLEMENTATION_PLAN.md Part 4, frozen). Same idiom as
`providers/base.py`'s `LLMProvider`/`SearchProvider` and
`providers/contact_base.py`'s `EnrichmentProvider` — a fourth provider
family, one Protocol shape.

V2-G implements ONLY the sending-IDENTITY half of this contract
(`connected_account_identifier()`), via `DemoEmailSendProvider` — there is
no sending in this checkpoint. `send()`/`find_sent_message()` are part of
the frozen Protocol so a later checkpoint's real `GmailSendProvider`
(V2-I) and `DemoEmailSendProvider.send()` (V2-H) slot in without a Protocol
change, but neither is implemented here, and neither is ever called in this
checkpoint — no `ActionProposal`/`ActionExecution` exists yet to call them.

Gmail itself is deployment-scoped, not run-scoped (see
`providers/live/google_oauth_runtime.py`'s module docstring) — this
Protocol and `DemoEmailSendProvider` are therefore deliberately NOT part of
`providers.base.ProviderBundle`; `ProviderBundle` stays exactly the three
fields V2-D/V2-DH already established (`llm`, `search`, `enrichment`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field


class SendAttemptStatus(StrEnum):
    """Mirrors `EnrichmentAttemptStatus`/`SearchAttemptStatus`'s shape —
    defined here, alongside the Protocol it belongs to, since no send call
    is ever issued in V2-G (a real send/reconcile telemetry seam is V2-I
    scope)."""

    OK = "OK"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class SendAttemptTelemetry(BaseModel):
    """One send-or-reconcile provider-call attempt — the send-side analogue
    of `EnrichmentAttemptTelemetry`. Unused in V2-G (nothing ever
    constructs one); defined now so `SendResult`/`ReconcileResult` below
    type-check against the frozen Part 4 shape."""

    provider: str
    operation: str  # "send" | "reconcile" — §3.3/§3.4, V2-I scope
    attempt: int = 1
    status: SendAttemptStatus = SendAttemptStatus.OK
    started_at: datetime
    finished_at: datetime
    latency_ms: float = 0.0
    http_status: int | None = None
    provider_request_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None  # redacted before this is set


class SendOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    PROVEN_NOT_DISPATCHED = "PROVEN_NOT_DISPATCHED"
    DEFINITIVE_REJECTION = "DEFINITIVE_REJECTION"
    ACCEPTANCE_UNKNOWN = "ACCEPTANCE_UNKNOWN"


class OutboundEmailMessage(BaseModel):
    to: str
    subject: str
    body_text: str
    message_id_header: str  # WE generate it; the provider must preserve it verbatim


class SendResult(BaseModel):
    outcome: SendOutcome
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    dispatched: bool  # was the body written to the transport? sets dispatched_at
    telemetry: list[SendAttemptTelemetry] = Field(default_factory=list)


class ReconcileStatus(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND_WITHIN_BOUNDS = "NOT_FOUND_WITHIN_BOUNDS"  # NOT evidence of non-delivery
    UNSUPPORTED = "UNSUPPORTED"  # provider cannot reconcile at all
    LOOKUP_FAILED = "LOOKUP_FAILED"  # the reconciliation call itself failed


class ReconcileBounds(BaseModel):
    page_size: int
    max_pages: int
    max_messages: int
    clock_skew_s: float


class ReconcileResult(BaseModel):
    status: ReconcileStatus
    provider_message_id: str | None = None
    messages_scanned: int = 0
    scanned_past_dispatch: bool = False
    telemetry: list[SendAttemptTelemetry] = Field(default_factory=list)


class LiveExternalEmailSendDisabled(Exception):
    """V2-H, Critical Decision D1 — a dedicated, typed, structural refusal
    for `EMAIL_SEND` + `LIVE_EXTERNAL`. Deliberately NOT `ProviderNotConfigured`
    (`providers/base.py`) and deliberately independent of whether any send
    provider is registered: registering a future `GmailSendProvider` (V2-I)
    must never silently make Live sending executable again just because a
    provider object now exists. This exception is the one and only thing
    that makes Live email sending unreachable in V2-H — `resolve_send_provider`
    (`providers/send_registry.py`) raises it unconditionally for `Mode.LIVE`,
    before any provider instance, `send()` call, or network dispatch is ever
    reachable.

    It stays load-bearing until V2-I *deliberately* removes/replaces it, and
    only after implementing the `claimed_email` suppression semantics named
    in `code`/`message` below — see docs/PROGRESS.md's "V2-H — D1" entry for
    the full carried-forward disposition.
    """

    code = "LIVE_EXTERNAL_EMAIL_SEND_DISABLED"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "Live external email sending is disabled in this checkpoint: "
                "(1) real Gmail sending is V2-I scope — no GmailSendProvider exists yet, and "
                "(2) Hunter 451/claimed_email suppression of a prior successful email observation "
                "in contact_channels remains an unresolved hard prerequisite before any external "
                "EMAIL_SEND path may be enabled. Configuring a future send provider must not silently "
                "lift this refusal — V2-I must remove it deliberately, after implementing (2)."
            )
        )


class EmailSendProvider(Protocol):
    name: str
    supports_message_id_lookup: bool

    async def connected_account_identifier(self) -> str | None:
        """The identity that WILL send, resolved fresh. An email address,
        never a credential (D13). Returns `None` when nothing is
        connected. Called at two moments in the frozen design (proposal
        creation and execute-time re-verification, V2-H/V2-I) — neither
        call site exists yet in V2-G."""
        ...

    async def send(self, msg: OutboundEmailMessage, *, idempotency_key: str) -> SendResult:
        """V2-H (Demo)/V2-I (Live) scope — not called anywhere in V2-G."""
        ...

    async def find_sent_message(
        self, *, message_id_header: str, sent_after: datetime, bounds: ReconcileBounds
    ) -> ReconcileResult:
        """V2-I scope (§3.3 bounded reconciliation) — not called anywhere
        in V2-G. `NOT_FOUND_WITHIN_BOUNDS` rather than `None` is the point:
        the type refuses to let a caller read "we didn't find it" as "it
        wasn't sent.\""""
        ...


class DemoEmailSendProvider:
    """V2-G: identity only (`connected_account_identifier()`). V2-H adds
    `send()` — the whole point of the Demo executor: zero-egress, fully
    synchronous, deterministic. `.invalid` is an IANA-reserved TLD that can
    never resolve, so this sending identity is structurally incapable of
    being a real person's address.

    `send()` performs NO network I/O of any kind (no socket, no DNS lookup,
    no HTTP call) — it deterministically constructs a `demo://` message id
    from the caller's own `idempotency_key` and returns `ACCEPTED`/
    `dispatched=True` unconditionally. There is no failure mode to simulate
    here: Demo Mode's job is to prove the governance path (proposal → hash
    → approval → execute → audit), not to exercise `SendOutcome`'s other
    members — those are exercised by `domain/action_policy.py`'s pure unit
    tests and, for a real provider, by V2-I.

    `find_sent_message()` still raises `NotImplementedError` — reconciliation
    is meaningless for a synchronous, always-immediately-settled send; V2-H
    never calls it.
    """

    name = "demo"
    supports_message_id_lookup = False

    async def connected_account_identifier(self) -> str | None:
        return "demo-sender@groundwork.invalid"

    async def send(self, msg: OutboundEmailMessage, *, idempotency_key: str) -> SendResult:
        now = datetime.now(timezone.utc)
        provider_message_id = f"demo://sent/{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)}"
        return SendResult(
            outcome=SendOutcome.ACCEPTED,
            provider_message_id=provider_message_id,
            provider_thread_id=None,
            dispatched=True,
            telemetry=[
                SendAttemptTelemetry(
                    provider=self.name,
                    operation="send",
                    attempt=1,
                    status=SendAttemptStatus.OK,
                    started_at=now,
                    finished_at=now,
                    latency_ms=0.0,
                )
            ],
        )

    async def find_sent_message(
        self, *, message_id_header: str, sent_after: datetime, bounds: ReconcileBounds
    ) -> ReconcileResult:
        raise NotImplementedError(
            "reconciliation is meaningless for DemoEmailSendProvider's synchronous, "
            "always-immediately-settled send — never called in V2-H"
        )
