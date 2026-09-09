"""Shared scaffolding for V2-G Gmail OAuth tests — a scripted
`httpx.MockTransport`-style fake for Google's token/revoke/profile
endpoints, mirroring `tests/live_enrichment_helpers.py`'s
`ScriptedApolloTransport`. No automated test may make a real Google API
call.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx

from groundwork.providers.live.google_oauth_runtime import (
    GOOGLE_PROFILE_URL,
    GOOGLE_REVOKE_URL,
    GOOGLE_TOKEN_URL,
    GoogleOAuthRuntime,
)

TEST_GOOGLE_CLIENT_ID = "test-google-client-id"
TEST_GOOGLE_CLIENT_SECRET = "test-google-client-secret-not-real"
TEST_REDIRECT_URI = "https://app.example.test/api/gmail/callback"


class _Settings:
    def __init__(self, **overrides: Any) -> None:
        self.google_client_id = TEST_GOOGLE_CLIENT_ID
        self.google_client_secret = TEST_GOOGLE_CLIENT_SECRET
        self.google_oauth_redirect_uri = TEST_REDIRECT_URI
        self.gmail_oauth_call_deadline_s = 5.0
        self.gmail_oauth_max_transport_retries = 1
        for k, v in overrides.items():
            setattr(self, k, v)


def token_response(*, access_token: str = "test-access-token", refresh_token: str | None = "test-refresh-token",
                    scope: str = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.metadata") -> dict[str, Any]:
    body: dict[str, Any] = {"access_token": access_token, "expires_in": 3599, "token_type": "Bearer", "scope": scope}
    if refresh_token is not None:
        body["refresh_token"] = refresh_token
    return body


def profile_response(*, email: str | None = "operator@example.com") -> dict[str, Any]:
    body: dict[str, Any] = {"historyId": "12345", "messagesTotal": 10, "threadsTotal": 5}
    if email is not None:
        body["emailAddress"] = email
    return body


class ScriptedGoogleTransport(httpx.AsyncBaseTransport):
    """Dispatches by URL: a per-endpoint handler, or a queued list of
    `(status_code, json_body)`/`Exception` steps consumed in call order for
    that endpoint. Records every request made, so tests can assert exactly
    how many (and which) calls happened."""

    def __init__(
        self,
        *,
        token_steps: list[tuple[int, dict] | Exception] | None = None,
        profile_steps: list[tuple[int, dict] | Exception] | None = None,
        revoke_steps: list[tuple[int, dict] | Exception] | None = None,
        handler: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> None:
        self._queues: dict[str, list[tuple[int, dict] | Exception]] = {
            GOOGLE_TOKEN_URL: list(token_steps or []),
            GOOGLE_PROFILE_URL: list(profile_steps or []),
            GOOGLE_REVOKE_URL: list(revoke_steps or []),
        }
        self.handler = handler
        self.requests: list[httpx.Request] = []

    @property
    def token_calls(self) -> int:
        return sum(1 for r in self.requests if str(r.url).startswith(GOOGLE_TOKEN_URL))

    @property
    def profile_calls(self) -> int:
        return sum(1 for r in self.requests if str(r.url).startswith(GOOGLE_PROFILE_URL))

    @property
    def revoke_calls(self) -> int:
        return sum(1 for r in self.requests if str(r.url).startswith(GOOGLE_REVOKE_URL))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler is not None:
            return self.handler(request)
        base = str(request.url).split("?", 1)[0]
        queue = self._queues.get(base)
        if not queue:
            raise AssertionError(f"no scripted step left for {base}")
        step = queue.pop(0)
        if isinstance(step, Exception):
            raise step
        status, body = step
        return httpx.Response(status, json=body, request=request)


def make_runtime(
    *,
    token_steps: list[tuple[int, dict] | Exception] | None = None,
    profile_steps: list[tuple[int, dict] | Exception] | None = None,
    revoke_steps: list[tuple[int, dict] | Exception] | None = None,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    settings_overrides: dict[str, Any] | None = None,
) -> tuple[GoogleOAuthRuntime, ScriptedGoogleTransport]:
    transport = ScriptedGoogleTransport(
        token_steps=token_steps, profile_steps=profile_steps, revoke_steps=revoke_steps, handler=handler
    )
    http_client = httpx.AsyncClient(transport=transport)
    runtime = GoogleOAuthRuntime.create(_Settings(**(settings_overrides or {})), http_client=http_client)
    return runtime, transport
