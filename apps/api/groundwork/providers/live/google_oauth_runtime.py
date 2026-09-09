"""Process-scoped Google OAuth runtime — V2-G (Gmail OAuth, connection
only, no sending).

DEPLOYMENT-scoped, not run-scoped: unlike `LiveProviderRuntime`/
`LiveSearchRuntime`/the enrichment runtimes, this has nothing to do with a
Groundwork *run* — it is never placed in `ProviderBundle`. It exists once
per process, for as long as an operator might connect/disconnect Gmail,
entirely independent of Demo/Live pipeline execution. Created once in
`main.py`'s lifespan (only when Google OAuth is actually configured — see
`google_oauth_configured()`) and closed once at shutdown, mirroring every
other `providers/live/*` runtime's lifecycle discipline exactly.

CRITICAL BOUNDARY (mirrors `apollo_enrichment.py`/`tavily_search.py`): this
module imports no repository, no SQLAlchemy, no DB table model —
`api/routers/gmail.py` alone persists anything. This module only talks to
Google and returns plain dicts/raises `GoogleOAuthError`.

No `GmailSendProvider` exists here or anywhere in this checkpoint (V2-I
scope) — this runtime only supports the connect/callback/disconnect flow:
minting an authorization URL, exchanging an authorization code (PKCE),
resolving the connected account via `users.getProfile`, and revoking a
token at disconnect.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from groundwork.config import settings as _settings_module

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

# EXACT scope set (§Part 9 of docs/V2_IMPLEMENTATION_PLAN.md) — `gmail.send`
# (the send itself, exercised only in V2-I) + `gmail.metadata` (headers
# only, the bounded SENT scan in §3.3, also V2-I). Deliberately NOT
# `gmail.readonly`/`openid`/`email`/`profile` — never request more than
# this, and never call Google's userinfo endpoint. Kept as a `tuple` (not a
# `set`) so the authorization URL's scope ordering is deterministic; tests
# assert `set(GMAIL_SCOPES) == {...}` for exact-set equality regardless.
GMAIL_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.metadata",
)


def google_oauth_configured() -> bool:
    """Both a client id/secret AND a configured redirect_uri are required —
    mirrors `operator_auth.operator_login_configured()`'s "all required
    pieces or none" discipline. Never a partial "works with just an id"
    state."""
    return bool(
        _settings_module.google_client_id
        and _settings_module.google_client_secret
        and _settings_module.google_oauth_redirect_uri
    )


class GoogleOAuthError(Exception):
    """Any transport failure (after the bounded retry) or definitive
    rejection talking to Google. Callers map this to the allow-listed
    `?gmail=error&reason=...` redirect — the raw message is logged
    (redacted) server-side, never reflected to the browser."""


def generate_pkce_pair() -> tuple[str, str]:
    """`(code_verifier, code_challenge)` — RFC 7636 S256. Pure, no network."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


@dataclass
class GoogleOAuthRuntime:
    client: httpx.AsyncClient
    client_id: str
    client_secret: str
    redirect_uri: str
    call_deadline_s: float
    max_transport_retries: int

    @classmethod
    def create(cls, settings: Any, *, http_client: httpx.AsyncClient | None = None) -> "GoogleOAuthRuntime":
        if not (settings.google_client_id and settings.google_client_secret and settings.google_oauth_redirect_uri):
            raise ValueError(
                "GoogleOAuthRuntime.create() requires google_client_id, google_client_secret, "
                "and google_oauth_redirect_uri"
            )
        return cls(
            client=http_client if http_client is not None else httpx.AsyncClient(),
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            redirect_uri=settings.google_oauth_redirect_uri,
            call_deadline_s=settings.gmail_oauth_call_deadline_s,
            max_transport_retries=settings.gmail_oauth_max_transport_retries,
        )

    async def close(self) -> None:
        await self.client.aclose()

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        """Pure — builds the URL, makes no request. `redirect_uri` is
        always the CONFIGURED value, never derived from a request header."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, Any]:
        """POST .../token, `authorization_code` grant. A received 4xx (a
        definitive OAuth-code-exchange rejection) is never retried — only a
        transport-level failure (no response received at all) gets the one
        bounded retry via `_post_with_retry`."""
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }
        response = await self._post_with_retry(GOOGLE_TOKEN_URL, data=data)
        if response.status_code != 200:
            raise GoogleOAuthError(f"token exchange failed: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise GoogleOAuthError("token exchange returned a non-JSON body") from exc
        if not isinstance(body, dict):
            raise GoogleOAuthError("token exchange returned a non-object body")
        return body

    async def refresh_access_token(self, *, refresh_token: str) -> str:
        """`refresh_token` grant — mints a fresh, short-lived access token
        from an already-stored (decrypted) refresh token. Used only by the
        manual, operator-run `scripts/gmail_scope_probe.py` (V2-G's hard
        gate) — the connect/callback flow above never needs this, since it
        already has a fresh access token straight from the code exchange.
        The minted access token is returned to the caller and is never
        persisted by this runtime."""
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        response = await self._post_with_retry(GOOGLE_TOKEN_URL, data=data)
        if response.status_code != 200:
            raise GoogleOAuthError(f"refresh_token exchange failed: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise GoogleOAuthError("refresh_token exchange returned a non-JSON body") from exc
        access_token = body.get("access_token") if isinstance(body, dict) else None
        if not access_token:
            raise GoogleOAuthError("refresh_token exchange response is missing access_token")
        return access_token

    async def get_profile(self, *, access_token: str) -> dict[str, Any]:
        """`GET users/me/profile` — reads `emailAddress` only. Never calls
        Google's userinfo endpoint. The access token is used here and
        nowhere else in this process; it is never persisted, logged, or
        returned by this method's caller."""
        try:
            response = await asyncio.wait_for(
                self.client.get(GOOGLE_PROFILE_URL, headers={"Authorization": f"Bearer {access_token}"}),
                timeout=self.call_deadline_s,
            )
        except (asyncio.TimeoutError, httpx.HTTPError) as exc:
            raise GoogleOAuthError(f"getProfile transport failure: {exc}") from exc
        if response.status_code != 200:
            raise GoogleOAuthError(f"getProfile failed: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise GoogleOAuthError("getProfile returned a non-JSON body") from exc
        if not isinstance(body, dict):
            raise GoogleOAuthError("getProfile returned a non-object body")
        return body

    async def revoke(self, *, token: str) -> bool:
        """Best-effort — callers must delete the local `gmail_connections`
        row regardless of this call's outcome (disconnect is safe by
        construction). Returns whether Google acknowledged the revoke
        (2xx); never raises."""
        try:
            response = await self._post_with_retry(GOOGLE_REVOKE_URL, data={"token": token})
        except GoogleOAuthError:
            return False
        return 200 <= response.status_code < 300

    async def _post_with_retry(self, url: str, *, data: dict[str, str]) -> httpx.Response:
        """One flat transport-retry loop, bounded at
        `1 + max_transport_retries` attempts — mirrors every other
        `providers/live/*` adapter's discipline. Only a transport-level
        failure (timeout, connection error — no response ever received) is
        retried; a received response (any status code) is returned as-is,
        so a definitive 4xx from Google is never retried."""
        attempts = 0
        while True:
            attempts += 1
            try:
                return await asyncio.wait_for(self.client.post(url, data=data), timeout=self.call_deadline_s)
            except (asyncio.TimeoutError, httpx.HTTPError) as exc:
                if attempts > self.max_transport_retries:
                    raise GoogleOAuthError(f"transport failure calling {url}: {exc}") from exc
