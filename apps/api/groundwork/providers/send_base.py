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

V2-I-b: `SendOutcome`/`ReconcileStatus` are imported from `models/enums.py`
— the frozen Part 4 canonical location, and the same classes
`domain/send_classifier.py` and `models/tables.py::ActionExecutionRow`
already use — rather than re-declared locally, so a `classify_send_outcome()`
result can be assigned straight into a `SendResult`/`ReconcileResult` field
without pydantic silently coercing across two same-named-but-distinct enum
classes (which would break `is`/exhaustive-match identity checks).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from groundwork.models.enums import ReconcileStatus, SendOutcome

__all__ = [
    "SendAttemptStatus",
    "SendAttemptTelemetry",
    "SendOutcome",
    "OutboundEmailMessage",
    "SendResult",
    "ReconcileStatus",
    "ReconcileBounds",
    "ReconcileResult",
    "LiveExternalEmailSendDisabled",
    "EmailSendProvider",
    "DemoEmailSendProvider",
]


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
    (`providers/base.py`).

    History: through V2-H/V2-I-a, and for part of V2-I-b, `resolve_send_
    provider(Mode.LIVE)` (`providers/send_registry.py`) raised this
    unconditionally, and `api/routers/actions.py::execute_action` called
    that function for `LIVE_EXTERNAL` `EMAIL_SEND` — making this the one and
    only thing that made Live email sending unreachable.

    V2-I-b status (final): removed from the real dispatch path ONLY after
    the accepted plan's full verification checklist actually PASSED in CI —
    full SQLite, full Postgres + migration drift, canonical Demo, and the
    complete safety-test matrix — and only on the user's explicit, separate
    authorization for that specific change (see `docs/PROGRESS.md`'s V2-I-b
    entry and PR #23 for the record). `execute_action`'s `LIVE_EXTERNAL`
    branch now dispatches via `api/gmail_provider_factory.py::
    build_gmail_send_provider` + `api/live_send_orchestration.py::
    dispatch_live_email_send` instead — never through `resolve_send_provider`.
    This class and `resolve_send_provider(Mode.LIVE)`'s unconditional raise
    are BOTH unchanged and still present — `resolve_send_provider` remains
    Demo-only and still raises this for `Mode.LIVE` if anything calls it
    that way (see that module's docstring); it simply is not on the real
    Live dispatch path any more.
    """

    code = "LIVE_EXTERNAL_EMAIL_SEND_DISABLED"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "resolve_send_provider(Mode.LIVE) is a defensive-only guard — a real GmailSendProvider "
                "exists and real Live dispatch is reachable (V2-I-b, CI-verified), but it goes through "
                "api/gmail_provider_factory.py::build_gmail_send_provider, never through this function."
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
