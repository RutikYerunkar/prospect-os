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


class TestReconciliation:
    async def test_found_returns_provider_message_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            assert "q=" not in url  # never uses the q parameter
            if "/messages/" in url and "/messages?" not in url and not url.endswith("/messages"):
                return httpx.Response(
                    200,
                    json={
                        "payload": {"headers": [{"name": "Message-ID", "value": "<abc@example.com>"}]},
                    },
                    request=request,
                )
            return httpx.Response(200, json={"messages": [{"id": "m1"}]}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await provider.find_sent_message(
            message_id_header="<abc@example.com>",
            sent_after=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            bounds=ReconcileBounds(page_size=25, max_pages=2, max_messages=50, clock_skew_s=60),
        )
        assert result.status is ReconcileStatus.FOUND
        assert result.provider_message_id == "m1"

    async def test_match_last_out_of_order_result_still_found(self):
        """Never depends on result ordering — the match is the LAST id in
        the list, not the first."""
        message_ids = ["newest", "middle", "oldest-actual-match"]
        headers_by_id = {
            "newest": "<other1@example.com>",
            "middle": "<other2@example.com>",
            "oldest-actual-match": "<abc@example.com>",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            for mid in message_ids:
                if f"/messages/{mid}" in url:
                    return httpx.Response(
                        200,
                        json={"payload": {"headers": [{"name": "Message-ID", "value": headers_by_id[mid]}]}},
                        request=request,
                    )
            return httpx.Response(200, json={"messages": [{"id": m} for m in message_ids]}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await provider.find_sent_message(
            message_id_header="<abc@example.com>",
            sent_after=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            bounds=ReconcileBounds(page_size=25, max_pages=2, max_messages=50, clock_skew_s=60),
        )
        assert result.status is ReconcileStatus.FOUND
        assert result.provider_message_id == "oldest-actual-match"
        assert result.messages_scanned == 3  # scanned the entire bounded set, no early stop

    async def test_matches_x_google_original_message_id_header(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if "/messages/m1" in url:
                return httpx.Response(
                    200,
                    json={
                        "payload": {
                            "headers": [{"name": "X-Google-Original-Message-ID", "value": "<abc@example.com>"}]
                        }
                    },
                    request=request,
                )
            return httpx.Response(200, json={"messages": [{"id": "m1"}]}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await provider.find_sent_message(
            message_id_header="<abc@example.com>",
            sent_after=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            bounds=ReconcileBounds(page_size=25, max_pages=2, max_messages=50, clock_skew_s=60),
        )
        assert result.status is ReconcileStatus.FOUND

    async def test_not_found_within_bounds_when_no_match(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            if "/messages/m1" in url:
                return httpx.Response(
                    200,
                    json={"payload": {"headers": [{"name": "Message-ID", "value": "<nomatch@example.com>"}]}},
                    request=request,
                )
            return httpx.Response(200, json={"messages": [{"id": "m1"}]}, request=request)

        provider, _ = make_provider(handler=handler)
        result = await provider.find_sent_message(
            message_id_header="<abc@example.com>",
            sent_after=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            bounds=ReconcileBounds(page_size=25, max_pages=2, max_messages=50, clock_skew_s=60),
        )
        assert result.status is ReconcileStatus.NOT_FOUND_WITHIN_BOUNDS

    async def test_lookup_failed_on_transport_error_never_converts_to_failed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            raise httpx.ConnectError("refused", request=request)

        provider, _ = make_provider(handler=handler)
        result = await provider.find_sent_message(
            message_id_header="<abc@example.com>",
            sent_after=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            bounds=ReconcileBounds(page_size=25, max_pages=2, max_messages=50, clock_skew_s=60),
        )
        assert result.status is ReconcileStatus.LOOKUP_FAILED

    async def test_never_uses_q_parameter_and_uses_label_ids_sent(self):
        seen_params: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            seen_params.append(dict(request.url.params))
            return httpx.Response(200, json={"messages": []}, request=request)

        provider, _ = make_provider(handler=handler)
        await provider.find_sent_message(
            message_id_header="<abc@example.com>",
            sent_after=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            bounds=ReconcileBounds(page_size=25, max_pages=2, max_messages=50, clock_skew_s=60),
        )
        assert seen_params, "expected at least one messages.list call"
        for params in seen_params:
            assert "q" not in params
            assert params.get("labelIds") == "SENT"

    async def test_bounded_page_and_message_caps_respected(self):
        call_log: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL

            if url.startswith(GOOGLE_TOKEN_URL):
                return httpx.Response(200, json={"access_token": "tok"})
            call_log.append(url)
            if "/messages/" in url and not url.rstrip("/").endswith("/messages"):
                return httpx.Response(
                    200, json={"payload": {"headers": [{"name": "Message-ID", "value": "<nomatch@x.com>"}]}}, request=request
                )
            # Always claim there's a next page with 5 new ids each time.
            page_token = dict(request.url.params).get("pageToken", "0")
            n = int(page_token)
            ids = [{"id": f"m{n}-{i}"} for i in range(5)]
            return httpx.Response(200, json={"messages": ids, "nextPageToken": str(n + 1)}, request=request)

        provider, _ = make_provider(handler=handler)
        bounds = ReconcileBounds(page_size=5, max_pages=2, max_messages=6, clock_skew_s=60)
        result = await provider.find_sent_message(
            message_id_header="<abc@example.com>",
            sent_after=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            bounds=bounds,
        )
        assert result.status is ReconcileStatus.NOT_FOUND_WITHIN_BOUNDS
        assert result.messages_scanned <= bounds.max_messages
        list_calls = [c for c in call_log if c.rstrip("/").endswith("/messages") or "labelIds" in c]
        # at most max_pages list calls
        assert len([c for c in call_log if "/messages?" in c or c.rstrip("/").endswith("/messages")]) <= bounds.max_pages
