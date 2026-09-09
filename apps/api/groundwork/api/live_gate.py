"""Live-gate helpers — Checkpoint I1 Phase 8.

`mode="live"` alone grants nothing: every Live read/write additionally
requires a valid operator session. Demo resources are always public. These
are small, explicit, per-route checks rather than one blanket dependency,
because whether a given resource is gated depends on THAT resource's own
`mode` field (a play's, a run's, a prospect's run's) — not the route path
statically.
"""

from __future__ import annotations

from fastapi import Request

from groundwork.api.errors import ForbiddenError, UnauthorizedError
from groundwork.config import settings

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def require_operator(is_operator: bool) -> None:
    if not is_operator:
        raise UnauthorizedError("operator session required")


def require_allowed_origin(request: Request) -> None:
    """CSRF guard for cookie-authenticated unsafe methods. CORS alone is
    NOT CSRF protection: CORS only governs whether a browser lets its own
    JavaScript *read* a cross-origin response — it does nothing to stop the
    browser from *sending* the request in the first place, cookies
    included. A missing `Origin` header or one that isn't in
    `settings.cors_origins` is rejected outright; there's no "policy allows
    missing Origin" mode here — a same-origin browser request always sends
    one for unsafe methods, so its absence means either a very old
    browser/non-browser client (fine to require the header from) or a
    deliberately stripped header (exactly what this guards against)."""
    if request.method not in _UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None or origin not in settings.cors_origins:
        raise ForbiddenError("missing or disallowed Origin header for this request")


def enforce_live_gate(request: Request, mode: str, is_operator: bool) -> None:
    """The one call every Live-touching route makes. No-ops entirely for
    `mode == "demo"` (Demo stays public and CSRF-unprotected — there's no
    real side effect or spend for a forged Demo request to exploit). For
    `mode == "live"`: requires a valid operator session (401 if absent),
    and — for unsafe methods only — validates `Origin` too (403 if missing/
    disallowed)."""
    if mode != "live":
        return
    require_operator(is_operator)
    require_allowed_origin(request)


def enforce_action_gate(request: Request, mode: str, is_operator: bool) -> None:
    """V2-H, Part 9 — the ONE deliberate deviation from `enforce_live_gate`'s
    shape: a governed-action endpoint (propose/approve/reject/execute) is
    `Origin`-checked even in Demo Mode, where every other endpoint stays
    CSRF-unprotected. A forged cross-origin Demo action request is harmless
    (D8 — no external side effect, zero egress), but it still writes durable
    `action_proposals`/`action_executions`/`action_events` audit rows, and
    the check costs nothing. Demo NEVER requires an operator session here —
    that is the whole point of D8's public, no-Gmail-connection-required
    Demo action path (criterion 4A). Live requires both, exactly like
    `enforce_live_gate`."""
    require_allowed_origin(request)
    if mode == "live":
        require_operator(is_operator)
