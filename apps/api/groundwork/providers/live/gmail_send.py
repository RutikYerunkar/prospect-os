"""`GmailSendProvider` — the real Live `EmailSendProvider` implementation
(V2-I-b, Phase 6).

Deployment-scoped, not run-scoped, and NOT part of `ProviderBundle` — exactly
like `GoogleOAuthRuntime` (see that module's docstring). Constructed per
dispatch by the caller (`api/routers/actions.py::execute_action`) from
already-resolved plain values. CRITICAL BOUNDARY, mirrored from every other
`providers/live/*` adapter: this module imports no repository, no
SQLAlchemy, no DB table model, and never touches the execution state
machine — the caller resolves the connected account identity and decrypted
refresh token via `GmailConnectionRepository`/`token_crypto` BEFORE
constructing this provider.

`messages.send` is a dedicated, zero-retry HTTP call — deliberately NOT
`GoogleOAuthRuntime._post_with_retry`'s bounded transport-retry loop. A
definitive rejection must never be retried, and an ambiguous outcome must
never be silently resent — retrying here would defeat the entire
acceptance-unknown taxonomy (§3.4, `domain/send_classifier.py`).

Registering this provider (wiring it into `main.py`'s lifespan/`app.state`)
does NOT by itself make Live sending reachable —
`providers/send_registry.py::resolve_send_provider(Mode.LIVE)` still
unconditionally raises `LiveExternalEmailSendDisabled` until that refusal is
deliberately removed (see docs/PROGRESS.md's refusal-removal gate).

Reconciliation (§3.3) — `find_sent_message()` — uses ONLY
`messages.list(labelIds=["SENT"])` (never `q`, never `gmail.readonly`) and
`messages.get(format="metadata", metadataHeaders=[...])`, and deliberately
does NOT early-stop on `internalDate`: it scans the entire bounded result
set (up to `bounds.max_pages`/`bounds.max_messages`) regardless of result
ordering, because Gmail's ordering is not a contract this code depends on.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from groundwork.domain.contact_identity import InvalidEmailIdentity, normalize_email_identity
from groundwork.domain.message_id import message_id_matches
from groundwork.domain.send_classifier import DispatchPhase, classify_send_outcome
from groundwork.models.enums import SendOutcome
from groundwork.providers.live.gmail_mime import build_raw_message
from groundwork.providers.live.google_oauth_runtime import GoogleOAuthError, GoogleOAuthRuntime
from groundwork.providers.send_base import (
    OutboundEmailMessage,
    ReconcileBounds,
    ReconcileResult,
    ReconcileStatus,
    SendAttemptStatus,
    SendAttemptTelemetry,
    SendResult,
)

GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_MESSAGES_LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"


def _gmail_message_url(message_id: str) -> str:
    return f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}"


# Never reached dispatch — the transport never wrote (or never finished
# establishing a connection in order to write) the request body.
_PRE_DISPATCH_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ProxyError,
    httpx.UnsupportedProtocol,
)


def _attempt_status(*, outcome: SendOutcome, http_status: int | None, error_type: str | None) -> SendAttemptStatus:
    if outcome is SendOutcome.ACCEPTED:
        return SendAttemptStatus.OK
    if http_status == 401:
        return SendAttemptStatus.AUTH_ERROR
    if http_status == 429:
        return SendAttemptStatus.RATE_LIMITED
    if http_status is not None and http_status >= 500:
        return SendAttemptStatus.PROVIDER_ERROR
    if error_type and "Timeout" in error_type:
        return SendAttemptStatus.TIMEOUT
    if error_type:
        return SendAttemptStatus.PROVIDER_ERROR
    return SendAttemptStatus.INVALID_RESPONSE


class GmailSendProvider:
    name = "gmail"
    supports_message_id_lookup = True

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        oauth_runtime: GoogleOAuthRuntime,
        refresh_token: str,
        connected_account_email: str,
        call_deadline_s: float,
    ) -> None:
        self._client = client
        self._oauth_runtime = oauth_runtime
        # Memory only — never logged, never persisted by this provider.
        self._refresh_token = refresh_token
        self._connected_account_email = connected_account_email
        self._call_deadline_s = call_deadline_s

    async def connected_account_identifier(self) -> str | None:
        try:
            return normalize_email_identity(self._connected_account_email)
        except InvalidEmailIdentity:
            return None

    # --- send ------------------------------------------------------------

    async def send(self, msg: OutboundEmailMessage, *, idempotency_key: str) -> SendResult:
        started = datetime.now(timezone.utc)
        try:
            access_token = await self._oauth_runtime.refresh_access_token(refresh_token=self._refresh_token)
            raw = build_raw_message(
                sender=self._connected_account_email,
                to=msg.to,
                subject=msg.subject,
                body_text=msg.body_text,
                message_id_header=msg.message_id_header,
                date=started,
            )
        except (GoogleOAuthError, ValueError) as exc:
            return self._send_result(
                phase=DispatchPhase.PRE_DISPATCH_FAILURE,
                started=started,
                finished=datetime.now(timezone.utc),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        # --- the ONE HTTP request — dedicated zero-retry path ---
        try:
            response = await self._client.post(
                GMAIL_SEND_URL,
                json={"raw": raw},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=self._call_deadline_s,
            )
        except _PRE_DISPATCH_EXCEPTIONS as exc:
            return self._send_result(
                phase=DispatchPhase.PRE_DISPATCH_FAILURE,
                started=started,
                finished=datetime.now(timezone.utc),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        except httpx.HTTPError as exc:
            return self._send_result(
                phase=DispatchPhase.POST_DISPATCH_UNKNOWN,
                started=started,
                finished=datetime.now(timezone.utc),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        finished = datetime.now(timezone.utc)
        message_id: str | None = None
        provider_request_id = response.headers.get("x-request-id")
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                candidate = body.get("id")
                if isinstance(candidate, str) and candidate:
                    message_id = candidate

        return self._send_result(
            phase=DispatchPhase.RESPONSE_RECEIVED,
            started=started,
            finished=finished,
            http_status=response.status_code,
            message_id=message_id,
            provider_request_id=provider_request_id,
        )

    def _send_result(
        self,
        *,
        phase: DispatchPhase,
        started: datetime,
        finished: datetime,
        http_status: int | None = None,
        message_id: str | None = None,
        provider_request_id: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> SendResult:
        outcome = classify_send_outcome(phase=phase, http_status=http_status, message_id=message_id)
        dispatched = phase is not DispatchPhase.PRE_DISPATCH_FAILURE
        latency_ms = max(0.0, (finished - started).total_seconds() * 1000.0)
        return SendResult(
            outcome=outcome,
            provider_message_id=message_id,
            provider_thread_id=None,
            dispatched=dispatched,
            telemetry=[
                SendAttemptTelemetry(
                    provider=self.name,
                    operation="send",
                    attempt=1,
                    status=_attempt_status(outcome=outcome, http_status=http_status, error_type=error_type),
                    started_at=started,
                    finished_at=finished,
                    latency_ms=latency_ms,
                    http_status=http_status,
                    provider_request_id=provider_request_id,
                    error_type=error_type,
                    error_message=error_message,
                )
            ],
        )

    # --- reconciliation (§3.3) -------------------------------------------

    async def find_sent_message(
        self, *, message_id_header: str, sent_after: datetime, bounds: ReconcileBounds
    ) -> ReconcileResult:
        started = datetime.now(timezone.utc)
        try:
            access_token = await self._oauth_runtime.refresh_access_token(refresh_token=self._refresh_token)
        except GoogleOAuthError as exc:
            return ReconcileResult(
                status=ReconcileStatus.LOOKUP_FAILED,
                messages_scanned=0,
                telemetry=[self._reconcile_telemetry(started, error_type=type(exc).__name__, error_message=str(exc))],
            )

        telemetry: list[SendAttemptTelemetry] = []
        messages_scanned = 0
        page_token: str | None = None
        pages = 0

        while pages < bounds.max_pages and messages_scanned < bounds.max_messages:
            list_started = datetime.now(timezone.utc)
            params: dict[str, str | int] = {"labelIds": "SENT", "maxResults": bounds.page_size}
            if page_token:
                params["pageToken"] = page_token
            try:
                list_response = await self._client.get(
                    GMAIL_MESSAGES_LIST_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=self._call_deadline_s,
                )
            except httpx.HTTPError as exc:
                telemetry.append(
                    self._reconcile_telemetry(list_started, error_type=type(exc).__name__, error_message=str(exc))
                )
                return ReconcileResult(
                    status=ReconcileStatus.LOOKUP_FAILED, messages_scanned=messages_scanned, telemetry=telemetry
                )
            pages += 1
            telemetry.append(self._reconcile_telemetry(list_started, http_status=list_response.status_code))
            if list_response.status_code != 200:
                return ReconcileResult(
                    status=ReconcileStatus.LOOKUP_FAILED, messages_scanned=messages_scanned, telemetry=telemetry
                )
            try:
                list_body = list_response.json()
            except ValueError:
                return ReconcileResult(
                    status=ReconcileStatus.LOOKUP_FAILED, messages_scanned=messages_scanned, telemetry=telemetry
                )
            ids = [m.get("id") for m in (list_body.get("messages") or []) if isinstance(m, dict) and m.get("id")]
            page_token = list_body.get("nextPageToken")

            for message_id in ids:
                if messages_scanned >= bounds.max_messages:
                    break
                get_started = datetime.now(timezone.utc)
                try:
                    get_response = await self._client.get(
                        _gmail_message_url(message_id),
                        params={"format": "metadata", "metadataHeaders": ["Message-ID", "X-Google-Original-Message-ID", "Date"]},
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=self._call_deadline_s,
                    )
                except httpx.HTTPError as exc:
                    telemetry.append(
                        self._reconcile_telemetry(get_started, error_type=type(exc).__name__, error_message=str(exc))
                    )
                    return ReconcileResult(
                        status=ReconcileStatus.LOOKUP_FAILED, messages_scanned=messages_scanned, telemetry=telemetry
                    )
                messages_scanned += 1
                telemetry.append(self._reconcile_telemetry(get_started, http_status=get_response.status_code))
                if get_response.status_code != 200:
                    continue
                try:
                    get_body = get_response.json()
                except ValueError:
                    continue
                headers = {
                    h.get("name"): h.get("value")
                    for h in (get_body.get("payload") or {}).get("headers", [])
                    if isinstance(h, dict)
                }
                if message_id_matches(
                    message_id_header,
                    message_id=headers.get("Message-ID"),
                    x_google_original_message_id=headers.get("X-Google-Original-Message-ID"),
                ):
                    return ReconcileResult(
                        status=ReconcileStatus.FOUND,
                        provider_message_id=message_id,
                        messages_scanned=messages_scanned,
                        scanned_past_dispatch=True,
                        telemetry=telemetry,
                    )

            if not page_token:
                break

        return ReconcileResult(
            status=ReconcileStatus.NOT_FOUND_WITHIN_BOUNDS,
            messages_scanned=messages_scanned,
            scanned_past_dispatch=messages_scanned > 0,
            telemetry=telemetry,
        )

    def _reconcile_telemetry(
        self,
        started: datetime,
        *,
        http_status: int | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> SendAttemptTelemetry:
        finished = datetime.now(timezone.utc)
        status = SendAttemptStatus.OK if http_status == 200 else _attempt_status(
            outcome=SendOutcome.ACCEPTANCE_UNKNOWN, http_status=http_status, error_type=error_type
        )
        return SendAttemptTelemetry(
            provider=self.name,
            operation="reconcile",
            attempt=1,
            status=status,
            started_at=started,
            finished_at=finished,
            latency_ms=max(0.0, (finished - started).total_seconds() * 1000.0),
            http_status=http_status,
            error_type=error_type,
            error_message=error_message,
        )
