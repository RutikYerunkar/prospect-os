"""`providers/live/gmail_send.py::GmailSendProvider` (V2-I-b, Phase 6) —
send-side outcome taxonomy end-to-end through the real classifier, and
reconciliation (§3.3) bounds/ordering/no-`q` behavior."""

from __future__ import annotations

import httpx
import pytest

from groundwork.models.enums import SendOutcome
from groundwork.providers.send_base import OutboundEmailMessage, ReconcileBounds, ReconcileStatus
from tests.live_gmail_send_helpers import make_provider


def test_gmail_send_provider_imports_no_repository_or_sqlalchemy():
    """Provider purity (mirrors `test_gmail_oauth.py`'s identical check for
    `GoogleOAuthRuntime`) — `providers/live/gmail_send.py` must never import
    a repository, SQLAlchemy, or a DB table model; the caller resolves the
    connection/refresh-token via `GmailConnectionRepository` BEFORE
    constructing this provider."""
    import ast
    import inspect

    from groundwork.providers.live import gmail_send

    tree = ast.parse(inspect.getsource(gmail_send))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert not name.startswith("sqlalchemy"), name
        assert not name.startswith("groundwork.repositories"), name
        assert name != "groundwork.models.tables", name


def test_gmail_mime_imports_no_repository_or_sqlalchemy_or_io():
    import ast
    import inspect

    from groundwork.providers.live import gmail_mime

    tree = ast.parse(inspect.getsource(gmail_mime))
    imported: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Attribute):
            calls.add(node.attr)
    for name in imported:
        assert not name.startswith("sqlalchemy"), name
        assert not name.startswith("groundwork.repositories"), name
        assert "httpx" not in name
    # No internal clock reads (`datetime.now`/`utcnow`) — every timestamp is
    # a caller-supplied parameter.
    assert "now" not in calls
    assert "utcnow" not in calls


def _msg() -> OutboundEmailMessage:
    return OutboundEmailMessage(
        to="prospect@example.com",
        subject="Hi",
        body_text="Hello there",
        message_id_header="<abc@example.com>",
    )


class TestSendTaxonomy:
    async def test_200_with_id_is_accepted_dispatched_true(self):
        provider, _ = make_provider(send_steps=[(200, {"id": "18abc"})])
        result = await provider.send(_msg(), idempotency_key="idem-1")
        assert result.outcome is SendOutcome.ACCEPTED
        assert result.dispatched is True
        assert result.provider_message_id == "18abc"

    async def test_200_without_id_is_acceptance_unknown(self):
        provider, _ = make_provider(send_steps=[(200, {})])
        result = await provider.send(_msg(), idempotency_key="idem-2")
        assert result.outcome is SendOutcome.ACCEPTANCE_UNKNOWN
        assert result.dispatched is True

    @pytest.mark.parametrize("status", [400, 401, 404])
    async def test_definitive_rejection_statuses(self, status):
        provider, _ = make_provider(send_steps=[(status, {"error": "rejected"})])
        result = await provider.send(_msg(), idempotency_key=f"idem-{status}")
        assert result.outcome is SendOutcome.DEFINITIVE_REJECTION
        assert result.dispatched is True

    @pytest.mark.parametrize("status", [403, 429, 500, 502, 503])
    async def test_ambiguous_statuses_stay_acceptance_unknown_regardless_of_body(self, status):
        for body in ({"error": "insufficient permission"}, {"error": "quota"}, {}):
            provider, _ = make_provider(send_steps=[(status, body)])
            result = await provider.send(_msg(), idempotency_key=f"idem-{status}-{body}")
            assert result.outcome is SendOutcome.ACCEPTANCE_UNKNOWN

    async def test_token_refresh_failure_is_proven_not_dispatched(self):
        provider, _ = make_provider(token_steps=[(401, {"error": "invalid_grant"})])
        result = await provider.send(_msg(), idempotency_key="idem-tok")
        assert result.outcome is SendOutcome.PROVEN_NOT_DISPATCHED
        assert result.dispatched is False

    async def test_connect_error_is_proven_not_dispatched(self):
        def handler(request: httpx.Request) -> httpx.Response:
            from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

            if str(request.url).startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            raise httpx.ConnectError("connection refused", request=request)

        provider, _ = make_provider(handler=handler)
        result = await provider.send(_msg(), idempotency_key="idem-connect")
        assert result.outcome is SendOutcome.PROVEN_NOT_DISPATCHED
        assert result.dispatched is False

    async def test_read_timeout_after_dispatch_is_acceptance_unknown(self):
        def handler(request: httpx.Request) -> httpx.Response:
            from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

            if str(request.url).startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            raise httpx.ReadTimeout("read timed out", request=request)

        provider, _ = make_provider(handler=handler)
        result = await provider.send(_msg(), idempotency_key="idem-readtimeout")
        assert result.outcome is SendOutcome.ACCEPTANCE_UNKNOWN
        assert result.dispatched is True

    async def test_connection_reset_mid_response_is_acceptance_unknown(self):
        def handler(request: httpx.Request) -> httpx.Response:
            from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

            if str(request.url).startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            raise httpx.RemoteProtocolError("connection reset", request=request)

        provider, _ = make_provider(handler=handler)
        result = await provider.send(_msg(), idempotency_key="idem-reset")
        assert result.outcome is SendOutcome.ACCEPTANCE_UNKNOWN

    async def test_unparseable_200_body_is_acceptance_unknown(self):
        def handler(request: httpx.Request) -> httpx.Response:
            from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

            if str(request.url).startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            return httpx.Response(200, content=b"not json", request=request)

        provider, _ = make_provider(handler=handler)
        result = await provider.send(_msg(), idempotency_key="idem-unparse")
        assert result.outcome is SendOutcome.ACCEPTANCE_UNKNOWN

    async def test_no_blind_retry_exactly_one_send_http_request(self):
        provider, transport = make_provider(send_steps=[(500, {"error": "server error"})])
        await provider.send(_msg(), idempotency_key="idem-noretry")
        send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
        assert len(send_calls) == 1

    async def test_send_result_never_dispatched_false_on_response_received(self):
        provider, _ = make_provider(send_steps=[(500, {})])
        result = await provider.send(_msg(), idempotency_key="idem-dispatched-flag")
        assert result.dispatched is True  # a response was received -> body WAS written




# --- V2-I-b correction (post-smoke): historyId-anchored reconciliation ----
# Replaces the prior generated-Message-ID matching approach. The real V2-I-b
# smoke send proved Gmail can omit BOTH `Message-ID` and
# `X-Google-Original-Message-ID` from `format=metadata` on the actually-sent
# message — see `test_reconciliation_survives_absent_message_id_headers`
# below, which documents that finding directly as a regression test.

from datetime import datetime, timedelta, timezone

from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

EXPECTED_SUBJECT = "Hi there"
EXPECTED_RECIPIENT = "prospect@example.com"
EXPECTED_SENDER = "operator@example.com"
WINDOW_START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
WINDOW_END = WINDOW_START + timedelta(minutes=5)
IN_WINDOW_DATE = "Thu, 1 Jan 2026 12:02:00 +0000"
OUT_OF_WINDOW_DATE = "Thu, 1 Jan 2026 13:00:00 +0000"


def _matching_headers(*, include_message_id: bool = False) -> list[dict]:
    headers = [
        {"name": "Subject", "value": EXPECTED_SUBJECT},
        {"name": "To", "value": f"Prospect <{EXPECTED_RECIPIENT}>"},
        {"name": "From", "value": f"Operator <{EXPECTED_SENDER}>"},
        {"name": "Date", "value": IN_WINDOW_DATE},
    ]
    if include_message_id:
        headers.append({"name": "Message-ID", "value": "<some-google-assigned-id@mail.gmail.com>"})
    return headers


def _history_body(message_ids: list[str], *, next_page_token: str | None = None) -> dict:
    body: dict = {"history": [{"id": "h1", "messagesAdded": [{"message": {"id": mid}} for mid in message_ids]}]}
    if next_page_token:
        body["nextPageToken"] = next_page_token
    return body


async def _find(provider, **overrides):
    kwargs = dict(
        pre_dispatch_history_id="1000",
        expected_subject=EXPECTED_SUBJECT,
        expected_recipient_identifier=EXPECTED_RECIPIENT,
        expected_sender_identifier=EXPECTED_SENDER,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        bounds=ReconcileBounds(page_size=25, max_pages=2, max_messages=50, clock_skew_s=60),
    )
    kwargs.update(overrides)
    return await provider.find_sent_message(**kwargs)


class TestHistoryCheckpoint:
    async def test_returns_history_id_string_on_success(self):
        provider, _ = make_provider(profile_steps=[(200, {"historyId": "424242"})])
        checkpoint = await provider.get_history_checkpoint()
        assert checkpoint == "424242"

    async def test_returns_none_on_token_refresh_failure(self):
        provider, _ = make_provider(token_steps=[(401, {"error": "invalid_grant"})])
        checkpoint = await provider.get_history_checkpoint()
        assert checkpoint is None

    async def test_returns_none_on_profile_http_failure(self):
        provider, _ = make_provider(profile_steps=[(500, {"error": "server error"})])
        checkpoint = await provider.get_history_checkpoint()
        assert checkpoint is None

    async def test_returns_none_when_history_id_missing_from_body(self):
        provider, _ = make_provider(profile_steps=[(200, {"messagesTotal": 5})])
        checkpoint = await provider.get_history_checkpoint()
        assert checkpoint is None

    async def test_returns_none_on_transport_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            raise httpx.ConnectError("refused", request=request)

        provider, _ = make_provider(handler=handler)
        checkpoint = await provider.get_history_checkpoint()
        assert checkpoint is None


class TestReconciliation:
    async def test_exactly_one_match_is_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            assert "q=" not in url  # never uses the q parameter
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(["m1"]), request=request)
            return httpx.Response(200, json={"payload": {"headers": _matching_headers()}}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.FOUND
        assert result.provider_message_id == "m1"

    async def test_reconciliation_survives_absent_message_id_headers(self):
        """Regression test, documenting the real V2-I-b smoke discovery
        directly: the exact sent message's `format=metadata` response
        carried NEITHER `Message-ID` NOR `X-Google-Original-Message-ID` —
        Subject/To/From/Date were present and matched. Reconciliation must
        succeed anyway; it must never read either header."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(["m1"]), request=request)
            # Deliberately NO Message-ID/X-Google-Original-Message-ID key at all.
            headers = [h for h in _matching_headers() if h["name"] not in ("Message-ID", "X-Google-Original-Message-ID")]
            assert not any(h["name"] in ("Message-ID", "X-Google-Original-Message-ID") for h in headers)
            return httpx.Response(200, json={"payload": {"headers": headers}}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.FOUND
        assert result.provider_message_id == "m1"

    async def test_zero_matches_is_not_found_within_bounds(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(["m1"]), request=request)
            headers = [h for h in _matching_headers() if h["name"] != "Subject"] + [
                {"name": "Subject", "value": "Completely different subject"}
            ]
            return httpx.Response(200, json={"payload": {"headers": headers}}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.NOT_FOUND_WITHIN_BOUNDS

    async def test_multiple_matches_is_ambiguous_never_picks_one(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(["m1", "m2"]), request=request)
            # BOTH candidates carry fully-matching metadata.
            return httpx.Response(200, json={"payload": {"headers": _matching_headers()}}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.AMBIGUOUS
        assert result.messages_scanned == 2  # both were scanned before deciding — no early stop

    async def test_candidate_ordering_cannot_affect_outcome(self):
        """The matching candidate is the LAST id returned, not the first —
        proves the scan doesn't stop at the first candidate."""
        message_ids = ["decoy1", "decoy2", "the-real-match"]

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(message_ids), request=request)
            for mid in message_ids:
                if url.split("?")[0].endswith(f"/messages/{mid}"):
                    if mid == "the-real-match":
                        return httpx.Response(200, json={"payload": {"headers": _matching_headers()}}, request=request)
                    mismatched = [h for h in _matching_headers() if h["name"] != "Subject"] + [
                        {"name": "Subject", "value": "not it"}
                    ]
                    return httpx.Response(200, json={"payload": {"headers": mismatched}}, request=request)
            raise AssertionError(f"unexpected message GET: {url}")

        provider, _ = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.FOUND
        assert result.provider_message_id == "the-real-match"
        assert result.messages_scanned == 3  # entire bounded set scanned regardless of order

    async def test_matching_requires_subject(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(["m1"]), request=request)
            headers = [h for h in _matching_headers() if h["name"] != "Subject"]  # Subject entirely absent
            return httpx.Response(200, json={"payload": {"headers": headers}}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.NOT_FOUND_WITHIN_BOUNDS

    async def test_matching_requires_normalized_to(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(["m1"]), request=request)
            headers = [h for h in _matching_headers() if h["name"] != "To"] + [
                {"name": "To", "value": "Someone Else <someone-else@example.com>"}
            ]
            return httpx.Response(200, json={"payload": {"headers": headers}}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.NOT_FOUND_WITHIN_BOUNDS

    async def test_matching_requires_normalized_from(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(["m1"]), request=request)
            headers = [h for h in _matching_headers() if h["name"] != "From"] + [
                {"name": "From", "value": "Someone Else <someone-else@example.com>"}
            ]
            return httpx.Response(200, json={"payload": {"headers": headers}}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.NOT_FOUND_WITHIN_BOUNDS

    async def test_matching_requires_valid_date(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(["m1"]), request=request)
            headers = [h for h in _matching_headers() if h["name"] != "Date"] + [
                {"name": "Date", "value": "not a real date"}
            ]
            return httpx.Response(200, json={"payload": {"headers": headers}}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.NOT_FOUND_WITHIN_BOUNDS

    async def test_matching_requires_date_within_window(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(["m1"]), request=request)
            headers = [h for h in _matching_headers() if h["name"] != "Date"] + [
                {"name": "Date", "value": OUT_OF_WINDOW_DATE}
            ]
            return httpx.Response(200, json={"payload": {"headers": headers}}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.NOT_FOUND_WITHIN_BOUNDS

    async def test_history_expired_on_404(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(404, json={"error": "startHistoryId too old"}, request=request)
            raise AssertionError("must not fetch any candidate after a 404 on history.list")

        provider, _ = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.HISTORY_EXPIRED
        assert result.messages_scanned == 0

    async def test_transport_failure_on_history_list_is_lookup_failed_never_resent(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            raise httpx.ConnectError("refused", request=request)

        provider, transport = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.LOOKUP_FAILED
        send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
        assert len(send_calls) == 0  # never resent

    async def test_transport_failure_on_message_get_is_lookup_failed_never_resent(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(["m1"]), request=request)
            raise httpx.ReadTimeout("timed out", request=request)

        provider, transport = make_provider(handler=handler)
        result = await _find(provider)
        assert result.status is ReconcileStatus.LOOKUP_FAILED
        send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
        assert len(send_calls) == 0

    async def test_gmail_4xx_5xx_during_reconciliation_never_causes_resend(self):
        for status in (403, 429, 500, 503):
            def handler(request: httpx.Request, status=status) -> httpx.Response:
                url = str(request.url)
                if url.startswith(GOOGLE_TOKEN_URL):
                    return httpx.Response(200, json={"access_token": "tok"})
                if url.split("?")[0].endswith("/history"):
                    return httpx.Response(status, json={"error": "boom"}, request=request)
                raise AssertionError("must not fetch a candidate after a failed history.list")

            provider, transport = make_provider(handler=handler)
            result = await _find(provider)
            assert result.status is ReconcileStatus.LOOKUP_FAILED
            assert not [r for r in transport.requests if "messages/send" in str(r.url)]

    async def test_never_uses_q_parameter(self):
        seen_params: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            seen_params.append(dict(request.url.params))
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body([]), request=request)
            raise AssertionError("no candidates expected")

        provider, _ = make_provider(handler=handler)
        await _find(provider)
        assert seen_params, "expected at least one history.list call"
        for params in seen_params:
            assert "q" not in params
            assert params.get("labelId") == "SENT"

    async def test_metadata_only_candidate_reads_no_body_no_full_no_raw(self):
        seen_get_params: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(200, json=_history_body(["m1"]), request=request)
            seen_get_params.append(dict(request.url.params))
            return httpx.Response(200, json={"payload": {"headers": _matching_headers()}}, request=request)

        provider, _ = make_provider(handler=handler)
        await _find(provider)
        assert seen_get_params, "expected at least one messages.get call"
        for params in seen_get_params:
            assert params.get("format") == "metadata"
            assert "raw" not in params.values()
            assert "full" not in params.values()

    async def test_bounded_page_and_message_caps_respected(self):
        call_log: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            call_log.append(url)
            if url.split("?")[0].endswith("/history"):
                page_token = dict(request.url.params).get("pageToken", "0")
                n = int(page_token)
                ids = [{"message": {"id": f"m{n}-{i}"}} for i in range(5)]
                return httpx.Response(
                    200,
                    json={"history": [{"id": "h", "messagesAdded": ids}], "nextPageToken": str(n + 1)},
                    request=request,
                )
            headers = [h for h in _matching_headers() if h["name"] != "Subject"] + [{"name": "Subject", "value": "no match"}]
            return httpx.Response(200, json={"payload": {"headers": headers}}, request=request)

        provider, _ = make_provider(handler=handler)
        bounds = ReconcileBounds(page_size=5, max_pages=2, max_messages=6, clock_skew_s=60)
        result = await _find(provider, bounds=bounds)
        assert result.status is ReconcileStatus.NOT_FOUND_WITHIN_BOUNDS
        assert result.messages_scanned <= bounds.max_messages
        history_calls = [c for c in call_log if c.split("?")[0].endswith("/history")]
        assert len(history_calls) <= bounds.max_pages

    async def test_pagination_cannot_affect_deterministic_outcome(self):
        """Same total candidate set, delivered across two pages instead of
        one — the outcome (which candidate matches) must be identical."""

        def handler_paginated(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                page_token = dict(request.url.params).get("pageToken")
                if page_token is None:
                    return httpx.Response(
                        200,
                        json={"history": [{"id": "h1", "messagesAdded": [{"message": {"id": "decoy"}}]}], "nextPageToken": "p2"},
                        request=request,
                    )
                return httpx.Response(
                    200,
                    json={"history": [{"id": "h2", "messagesAdded": [{"message": {"id": "m1"}}]}]},
                    request=request,
                )
            if url.split("?")[0].endswith("/messages/decoy"):
                mismatched = [h for h in _matching_headers() if h["name"] != "Subject"] + [{"name": "Subject", "value": "no"}]
                return httpx.Response(200, json={"payload": {"headers": mismatched}}, request=request)
            return httpx.Response(200, json={"payload": {"headers": _matching_headers()}}, request=request)

        provider, _ = make_provider(handler=handler_paginated)
        result = await _find(provider, bounds=ReconcileBounds(page_size=1, max_pages=3, max_messages=10, clock_skew_s=60))
        assert result.status is ReconcileStatus.FOUND
        assert result.provider_message_id == "m1"
