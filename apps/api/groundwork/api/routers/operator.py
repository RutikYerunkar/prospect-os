"""`POST/DELETE /api/operator/session` — Checkpoint I1 Phase 8.

The only way to obtain the operator session cookie that unlocks Live Mode.
No users table, no OAuth, no organizations — one shared passphrase
(`OPERATOR_PASSPHRASE`), compared in constant time, gates a signed, HttpOnly
cookie. `OPERATOR_PASSPHRASE` (or `SESSION_SIGNING_KEY`) unset means this
endpoint always 401s — Live stays hard-disabled with no other path to a
session.

Never logs: the passphrase, the request body, the cookie value, or the
signing key. Failed attempts are rate-limited per client IP.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from groundwork.api.errors import TooManyRequestsError, UnauthorizedError
from groundwork.api.live_gate import require_allowed_origin
from groundwork.api.operator_auth import (
    COOKIE_NAME,
    create_session_cookie_value,
    operator_login_configured,
    passphrase_matches,
)
from groundwork.api.rate_limit import SlidingWindowRateLimiter
from groundwork.config import settings

router = APIRouter(prefix="/api/operator", tags=["operator"])

# Process-local — correct for this project's one-instance/one-worker
# deployment shape (see docs/DEPLOYMENT.md), not a distributed rate limit.
_login_limiter = SlidingWindowRateLimiter(
    max_attempts=settings.operator_login_rate_limit_attempts,
    window_s=settings.operator_login_rate_limit_window_s,
)


class OperatorLoginRequest(BaseModel):
    passphrase: str = Field(min_length=1, max_length=512)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _cookie_kwargs() -> dict:
    return {
        "path": "/",
        "httponly": True,
        # Secure in production; allowed to be non-Secure only for plain-
        # HTTP local dev, where a Secure cookie simply wouldn't be sent at
        # all. Host-only cookie — no `domain=` is ever passed, deliberately
        # (see CLAUDE.md's I2 note on sibling app/API subdomains: a shared-
        # Domain cookie is a later decision, not this checkpoint's).
        "secure": settings.environment == "production",
        "samesite": "lax",
    }


@router.post("/session")
async def create_operator_session(body: OperatorLoginRequest, request: Request, response: Response) -> dict:
    require_allowed_origin(request)

    if not operator_login_configured():
        # Never a silent "works anyway" — an operator deployment missing
        # either OPERATOR_PASSPHRASE or SESSION_SIGNING_KEY is exactly the
        # "Live hard-disabled" state, surfaced honestly rather than as a
        # confusing "wrong passphrase."
        raise UnauthorizedError("operator login is not configured on this deployment")

    key = _client_key(request)
    if _login_limiter.is_blocked(key):
        raise TooManyRequestsError("too many failed operator login attempts — try again later")

    if not passphrase_matches(body.passphrase):
        _login_limiter.record_failure(key)
        raise UnauthorizedError("invalid passphrase")

    _login_limiter.reset(key)
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_cookie_value(),
        max_age=settings.session_max_age_s,
        **_cookie_kwargs(),
    )
    return {"status": "ok"}


@router.delete("/session")
async def delete_operator_session(request: Request, response: Response) -> dict:
    require_allowed_origin(request)
    response.delete_cookie(key=COOKIE_NAME, **_cookie_kwargs())
    return {"status": "ok"}
