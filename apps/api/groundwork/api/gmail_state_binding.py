"""OAuth `state` <-> initiating-operator-session binding (V2-G, approved
Rev 2 construction) — a pure module beside `operator_auth.py`, not a
modification to it.

Existing operator sessions are an itsdangerous cookie whose payload is the
constant `"operator"` (see `operator_auth.py`) — there is no independent
per-session id to bind against. This module instead binds the OAuth `state`
to the exact initiating bearer cookie VALUE:

    state_id     = secrets.token_urlsafe(32)
    binding_tag  = HMAC-SHA256(key=SESSION_SIGNING_KEY,
                                message=STATE_BINDING_VERSION + "|" + state_id
                                        + "|" + operator_cookie_value)
    state_param  = state_id + "." + binding_tag

Only `state_id` (plus the PKCE verifier/timestamps) is ever persisted in
`oauth_states` — the binding tag and the operator cookie value are never
written to the database. At callback time the tag is recomputed from the
CURRENT operator cookie and compared with `hmac.compare_digest`; a mismatch
means either a different browser/session or state that was never minted for
this session, and is rejected before any token exchange.

This is NOT a new session/auth system — it reuses
`SESSION_SIGNING_KEY`/`SESSION_SIGNING_KEY_OLD` exactly as configured for
the operator cookie itself. Verification tries the current key first, then
the old one (rotation support, mirroring
`operator_auth.verify_session_cookie`'s order); minting always uses the
current key.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from groundwork.config import settings

STATE_BINDING_VERSION = "gmail-oauth-state-binding-v1"
_SEPARATOR = "."

# `secrets.token_urlsafe()` output charset (base64url, no padding).
_STATE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# A SHA-256 HMAC hexdigest is always exactly 64 lowercase hex characters.
_TAG_RE = re.compile(r"^[0-9a-f]{64}$")


class MalformedStateParam(ValueError):
    """The `state` query parameter is not well-formed — never a signal
    about WHOSE state it is, only that its shape is unparseable. Maps to a
    400 with zero DB mutation and zero token exchange."""


def _tag(*, state_id: str, operator_cookie_value: str, key: str) -> str:
    message = f"{STATE_BINDING_VERSION}|{state_id}|{operator_cookie_value}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def mint_state_param(operator_cookie_value: str) -> tuple[str, str]:
    """Returns `(state_id, state_param)`. Minting ALWAYS uses the current
    `SESSION_SIGNING_KEY` — never `SESSION_SIGNING_KEY_OLD`."""
    if not settings.session_signing_key:
        raise RuntimeError("SESSION_SIGNING_KEY is not configured — cannot mint an OAuth state binding")
    state_id = secrets.token_urlsafe(32)
    tag = _tag(state_id=state_id, operator_cookie_value=operator_cookie_value, key=settings.session_signing_key)
    return state_id, f"{state_id}{_SEPARATOR}{tag}"


def parse_state_param(state_param: str) -> tuple[str, str]:
    """Requires EXACTLY one separator and two well-formed, non-empty
    components. Raises `MalformedStateParam` for anything else — the caller
    must map this to 400 without touching the database."""
    parts = state_param.split(_SEPARATOR)
    if len(parts) != 2:
        raise MalformedStateParam("state parameter must contain exactly one separator")
    state_id, tag = parts
    if not state_id or not tag:
        raise MalformedStateParam("state parameter has an empty component")
    if not _STATE_ID_RE.match(state_id):
        raise MalformedStateParam("state parameter's id component is not well-formed")
    if not _TAG_RE.match(tag):
        raise MalformedStateParam("state parameter's binding tag is not well-formed")
    return state_id, tag


def verify_binding(*, state_id: str, tag: str, operator_cookie_value: str) -> bool:
    """Current key first, then `SESSION_SIGNING_KEY_OLD` if configured —
    same rotation order as `operator_auth.verify_session_cookie`. Constant-
    time comparison (`hmac.compare_digest`) against each candidate."""
    if settings.session_signing_key:
        expected = _tag(state_id=state_id, operator_cookie_value=operator_cookie_value, key=settings.session_signing_key)
        if hmac.compare_digest(expected, tag):
            return True
    if settings.session_signing_key_old:
        expected_old = _tag(
            state_id=state_id, operator_cookie_value=operator_cookie_value, key=settings.session_signing_key_old
        )
        if hmac.compare_digest(expected_old, tag):
            return True
    return False
