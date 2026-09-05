"""`/api/gmail/*` — V2-G: Gmail OAuth connection only, no sending.

Every route is operator-gated in BOTH Demo and Live (docs/
V2_IMPLEMENTATION_PLAN.md Part 9: "connect/disconnect Gmail: operator only —
no demo equivalent exists"). The callback route additionally implements the
exact security-critical ordering from the approved Rev 2 plan — see the
docstring on `callback()` below.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from groundwork.api import gmail_state_binding
from groundwork.api.deps import GmailRepoDep, GoogleOAuthRuntimeDep, IsOperatorDep
from groundwork.api.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    TooManyRequestsError,
    UnauthorizedError,
    UnprocessableEntityError,
)
from groundwork.api.live_gate import require_allowed_origin, require_operator
from groundwork.api.operator_auth import COOKIE_NAME, verify_session_cookie
from groundwork.api.rate_limit import SlidingWindowRateLimiter
from groundwork.api.schemas import GmailConnectionResponse, GmailConnectResponse, GmailDisconnectResponse
from groundwork.config import settings
from groundwork.observability.redact import redact
from groundwork.providers.live.google_oauth_runtime import GMAIL_SCOPES, GoogleOAuthError, generate_pkce_pair
from groundwork.timeutil import utcnow
from groundwork.token_crypto import TokenEncryptionError, decrypt_refresh_token, encrypt_refresh_token

router = APIRouter(prefix="/api/gmail", tags=["gmail"])
logger = logging.getLogger(__name__)

# Process-local, same posture as the operator-login limiter (`api/
# rate_limit.py`) — counts callback FAILURES (a state/session binding
# mismatch), not every callback request.
_callback_failure_limiter = SlidingWindowRateLimiter(
    max_attempts=settings.gmail_callback_failure_rate_limit_attempts,
    window_s=settings.gmail_callback_failure_rate_limit_window_s,
)

# Google's OAuth `error` query parameter is never reflected to the browser
# verbatim (never a raw-error-text passthrough). Only allow-listed values
# map through; anything else — including a genuinely unrecognized future
# Google error code — collapses to "unknown".
_ALLOWED_GOOGLE_ERROR_REASONS = {"access_denied"}


def _sanitize_error_reason(raw: str) -> str:
    return raw if raw in _ALLOWED_GOOGLE_ERROR_REASONS else "unknown"


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/connection", response_model=GmailConnectionResponse)
async def get_connection(is_operator: IsOperatorDep, repo: GmailRepoDep) -> GmailConnectionResponse:
    require_operator(is_operator)
    row = await repo.get_connection()
    if row is None or not row.google_account_email:
        return GmailConnectionResponse(connected=False)
    return GmailConnectionResponse(
        connected=True,
        google_account_email=row.google_account_email,
        scopes=row.scopes,
        connected_at=row.connected_at,
        connected_by_actor=row.connected_by_actor,
        last_refreshed_at=row.last_refreshed_at,
    )


@router.post("/connect", response_model=GmailConnectResponse)
async def connect(
    request: Request, is_operator: IsOperatorDep, repo: GmailRepoDep, runtime: GoogleOAuthRuntimeDep
) -> GmailConnectResponse:
    require_operator(is_operator)
    require_allowed_origin(request)

    if runtime is None:
        raise UnprocessableEntityError("Gmail OAuth is not configured on this deployment")

    operator_cookie_value = request.cookies.get(COOKIE_NAME)
    if not operator_cookie_value:
        # `require_operator` already passed, so this should be unreachable
        # in practice — fail closed rather than mint a binding against an
        # empty string if it somehow isn't.
        raise UnauthorizedError("operator session required")

    # Opportunistic cleanup (§Backend routes) — never load-bearing for
    # correctness; `consume_state`'s own `expires_at` check already rejects
    # an expired-but-not-yet-deleted row.
    await repo.delete_expired_states(before=utcnow())

    state_id, state_param = gmail_state_binding.mint_state_param(operator_cookie_value)
    code_verifier, code_challenge = generate_pkce_pair()
    await repo.create_state(state_id=state_id, pkce_verifier=code_verifier, ttl_s=settings.gmail_oauth_state_ttl_s)

    authorization_url = runtime.authorization_url(state=state_param, code_challenge=code_challenge)
    return GmailConnectResponse(authorization_url=authorization_url)


@router.get("/callback")
async def callback(
    request: Request,
    repo: GmailRepoDep,
    runtime: GoogleOAuthRuntimeDep,
    state: str = Query(...),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Security-critical ordering (approved Rev 2 plan) — do not reorder:

    1. Parse `state`. Malformed -> 400, no DB mutation.
    2. Require + verify the operator session cookie. Missing/invalid -> 401,
       no DB mutation, no exchange.
    3. Verify the state/session HMAC binding. Mismatch -> 403, the
       `oauth_states` row is NOT consumed, no exchange, and the callback-
       failure limiter is incremented.
    4. Consume `state_id` via ONE guarded UPDATE. `rowcount != 1` -> 409, no
       exchange.
    5. Only now inspect Google's `error` parameter.
    6. If no error: require `code`, exchange it (PKCE), call
       `users.getProfile`, encrypt the refresh token, persist the
       connection, redirect to `/settings?gmail=connected`.

    A mismatched operator session (step 3) never consumes the row. A replay
    with the CORRECT session (step 4, second call) fails at the guarded
    UPDATE — both by construction, not by an extra check.
    """
    client_key = _client_key(request)
    if _callback_failure_limiter.is_blocked(client_key):
        raise TooManyRequestsError("too many failed Gmail OAuth callback attempts — try again later")

    # --- 1. Parse state — malformed -> 400, no DB mutation ---------------
    try:
        state_id, tag = gmail_state_binding.parse_state_param(state)
    except gmail_state_binding.MalformedStateParam as exc:
        raise BadRequestError(str(exc)) from exc

    # --- 2. Require + verify operator session — 401, no DB mutation ------
    operator_cookie_value = request.cookies.get(COOKIE_NAME)
    if not verify_session_cookie(operator_cookie_value):
        raise UnauthorizedError("operator session required")

    # --- 3. Verify state/session binding — 403, do NOT consume -----------
    if not gmail_state_binding.verify_binding(
        state_id=state_id, tag=tag, operator_cookie_value=operator_cookie_value
    ):
        _callback_failure_limiter.record_failure(client_key)
        raise ForbiddenError("OAuth state does not match the initiating operator session")

    # --- 4. Consume via ONE guarded UPDATE — 409 on anything but exactly one row
    consumed = await repo.consume_state(state_id)
    if consumed is None:
        raise ConflictError("OAuth state was already used, does not exist, or has expired")

    # --- 5. Only now inspect Google's error parameter ---------------------
    if error is not None:
        return RedirectResponse(
            url=f"/settings?gmail=error&reason={_sanitize_error_reason(error)}", status_code=303
        )

    # --- 6. No error: exchange the code, resolve identity, persist -------
    if not code or runtime is None:
        return RedirectResponse(url="/settings?gmail=error&reason=unknown", status_code=303)

    try:
        token_response = await runtime.exchange_code(code=code, code_verifier=consumed.pkce_verifier)
        access_token = token_response.get("access_token")
        refresh_token = token_response.get("refresh_token")
        granted_scope = token_response.get("scope") or ""
        if not access_token or not refresh_token:
            raise GoogleOAuthError("token response is missing access_token/refresh_token")

        profile = await runtime.get_profile(access_token=access_token)
        google_account_email = profile.get("emailAddress")
        if not google_account_email:
            # Fail closed — never guess, never derive from request input,
            # never persist a connection without a resolved identity.
            raise GoogleOAuthError("getProfile response is missing emailAddress")

        ciphertext, key_version = encrypt_refresh_token(refresh_token)
        now = utcnow()
        await repo.upsert_connection(
            google_account_email=google_account_email,
            encrypted_refresh_token=ciphertext,
            key_version=key_version,
            scopes=granted_scope.split() if granted_scope else list(GMAIL_SCOPES),
            connected_at=now,
            connected_by_actor="operator",
            last_refreshed_at=now,
        )
    except (GoogleOAuthError, TokenEncryptionError) as exc:
        logger.warning("gmail oauth callback failed: %s", redact(str(exc)))
        return RedirectResponse(url="/settings?gmail=error&reason=unknown", status_code=303)

    return RedirectResponse(url="/settings?gmail=connected", status_code=303)


@router.delete("/connection", response_model=GmailDisconnectResponse)
async def disconnect(
    request: Request, is_operator: IsOperatorDep, repo: GmailRepoDep, runtime: GoogleOAuthRuntimeDep
) -> GmailDisconnectResponse:
    require_operator(is_operator)
    require_allowed_origin(request)

    row = await repo.get_connection()
    revoked = False
    if row is not None and row.encrypted_refresh_token and runtime is not None:
        try:
            plaintext = decrypt_refresh_token(row.encrypted_refresh_token, row.key_version)
            revoked = await runtime.revoke(token=plaintext)
        except TokenEncryptionError:
            # Never retain a locally usable token just because it couldn't
            # be decrypted — fall through to deleting the row regardless.
            revoked = False

    deleted = await repo.delete_connection()
    # Safe telemetry only — never the token/ciphertext.
    logger.info("gmail connection disconnected: revoked_at_google=%s deleted=%s", revoked, deleted)
    return GmailDisconnectResponse(status="ok", deleted=deleted)
