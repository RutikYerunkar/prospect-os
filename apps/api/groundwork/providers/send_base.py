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

from datetime import datetime
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
    """V2-G: identity only. `.invalid` is an IANA-reserved TLD that can
    never resolve, so this sending identity is structurally incapable of
    being a real person's address — zero network I/O, exactly like the
    `demo-sender@groundwork.invalid` constant the frozen plan names.

    `send()`/`find_sent_message()` deliberately raise `NotImplementedError`
    — no `ActionProposal`/`ActionExecution` exists yet to ever call them
    (V2-H/V2-I scope); this class exists in V2-G purely so
    `connected_account_identifier()` has a concrete, testable
    implementation satisfying the frozen Protocol shape.
    """

    name = "demo"
    supports_message_id_lookup = False

    async def connected_account_identifier(self) -> str | None:
        return "demo-sender@groundwork.invalid"

    async def send(self, msg: OutboundEmailMessage, *, idempotency_key: str) -> SendResult:
        raise NotImplementedError("DemoEmailSendProvider.send() is V2-H scope — not implemented in V2-G")

    async def find_sent_message(
        self, *, message_id_header: str, sent_after: datetime, bounds: ReconcileBounds
    ) -> ReconcileResult:
        raise NotImplementedError("reconciliation is V2-I scope — not implemented in V2-G")
