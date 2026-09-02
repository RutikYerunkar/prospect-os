"""Operator session — Checkpoint I1 Phase 8.

Minimal operator access, deliberately: NO users table, NO OAuth, NO
organizations, NO SaaS auth. A single shared passphrase
(`OPERATOR_PASSPHRASE`) gates every Live Mode read/write behind one signed,
HttpOnly session cookie. `OPERATOR_PASSPHRASE` unset (or `SESSION_SIGNING_KEY`
unset) means Live is hard-disabled — there is structurally no other way to
obtain a session, and the gate is enforced here, in the API, never only in
the frontend: a caller sending `mode="live"` without a valid operator
session gets no Live capability, full stop.

The cookie carries no claims worth stealing — its value is just an
itsdangerous-signed, timestamped token; validity IS the session. Rotate
`SESSION_SIGNING_KEY` by setting the old value as `SESSION_SIGNING_KEY_OLD`
(accepted for verification only — new cookies always sign with the current
key) and existing sessions survive the rotation until they'd have expired
anyway.
"""

from __future__ import annotations

import hmac

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from groundwork.config import settings

COOKIE_NAME = "groundwork_operator_session"
_SALT = "groundwork-operator-session-v1"
_PAYLOAD = "operator"


def _serializer(key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(key, salt=_SALT)


def operator_login_configured() -> bool:
    """Both a passphrase to check AND a key to sign with are required —
    a passphrase alone with no signing key would mean "issue an unsigned
    cookie," which this never does (fail closed, not fail open)."""
    return bool(settings.operator_passphrase) and bool(settings.session_signing_key)


def passphrase_matches(candidate: str) -> bool:
    """Constant-time comparison (`hmac.compare_digest`) — a naive `==`
    would leak how many leading characters matched via response timing."""
    if not settings.operator_passphrase:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), settings.operator_passphrase.encode("utf-8"))


def create_session_cookie_value() -> str:
    if not settings.session_signing_key:
        raise RuntimeError("SESSION_SIGNING_KEY is not configured — cannot issue a session cookie")
    return _serializer(settings.session_signing_key).dumps(_PAYLOAD)


def verify_session_cookie(value: str | None) -> bool:
    """Tries the current signing key first, then the old one (rotation
    support, verification-only). Returns `False` for any missing/malformed/
    expired/mis-signed value — never raises, so a garbage cookie from a
    stale browser session just reads as "not an operator," not a 500."""
    if not value or not settings.session_signing_key:
        return False
    max_age = settings.session_max_age_s
    try:
        _serializer(settings.session_signing_key).loads(value, max_age=max_age)
        return True
    except (BadSignature, SignatureExpired):
        pass
    if settings.session_signing_key_old:
        try:
            _serializer(settings.session_signing_key_old).loads(value, max_age=max_age)
            return True
        except (BadSignature, SignatureExpired):
            pass
    return False
