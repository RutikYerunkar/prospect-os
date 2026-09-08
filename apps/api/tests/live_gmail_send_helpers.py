"""Scripted `httpx.MockTransport`-style fake for Gmail's `messages.send` /
`messages.list` / `messages.get` endpoints — mirrors
`tests/gmail_oauth_helpers.py::ScriptedGoogleTransport`. No automated test
may make a real Gmail API call."""

from __future__ import annotations

from typing import Any, Callable

import httpx

from groundwork.providers.live.gmail_send import GMAIL_MESSAGES_LIST_URL, GMAIL_SEND_URL
from groundwork.providers.live.gmail_send import GmailSendProvider
from groundwork.providers.live.google_oauth_runtime import GOOGLE_TOKEN_URL


class ScriptedGmailTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        token_steps: list[tuple[int, dict] | Exception] | None = None,
        send_steps: list[tuple[int, dict] | Exception] | None = None,
        handler: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> None:
        self._token_queue: list[tuple[int, dict] | Exception] = list(token_steps or [(200, {"access_token": "tok"})])
        self._send_queue: list[tuple[int, dict] | Exception] = list(send_steps or [])
        self.handler = handler
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?", 1)[0]
        if self.handler is not None:
            return self.handler(request)
        if url == GOOGLE_TOKEN_URL:
            step = self._token_queue.pop(0)
        elif url == GMAIL_SEND_URL:
            step = self._send_queue.pop(0)
        else:
            raise AssertionError(f"unscripted URL: {url}")
        if isinstance(step, Exception):
            raise step
        status, body = step
        return httpx.Response(status, json=body, request=request)


def make_provider(
    *,
    token_steps: list[tuple[int, dict] | Exception] | None = None,
    send_steps: list[tuple[int, dict] | Exception] | None = None,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    connected_account_email: str = "operator@example.com",
    call_deadline_s: float = 5.0,
) -> tuple[GmailSendProvider, ScriptedGmailTransport]:
    transport = ScriptedGmailTransport(token_steps=token_steps, send_steps=send_steps, handler=handler)
    client = httpx.AsyncClient(transport=transport)

    class _FakeOAuthRuntime:
        def __init__(self, c: httpx.AsyncClient) -> None:
            self.client = c

        async def refresh_access_token(self, *, refresh_token: str) -> str:
            response = await self.client.post(GOOGLE_TOKEN_URL, data={"refresh_token": refresh_token})
            if response.status_code != 200:
                from groundwork.providers.live.google_oauth_runtime import GoogleOAuthError

                raise GoogleOAuthError(f"refresh failed: HTTP {response.status_code}")
            body = response.json()
            token = body.get("access_token")
            if not token:
                from groundwork.providers.live.google_oauth_runtime import GoogleOAuthError

                raise GoogleOAuthError("missing access_token")
            return token

    runtime = _FakeOAuthRuntime(client)
    provider = GmailSendProvider(
        client=client,
        oauth_runtime=runtime,  # type: ignore[arg-type]
        refresh_token="refresh-token-not-real",
        connected_account_email=connected_account_email,
        call_deadline_s=call_deadline_s,
    )
    return provider, transport
