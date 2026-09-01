"""URL safety and canonicalization (H1 Phase 3).

Pure, offline — no DNS resolution, no network calls of any kind. This
project's architecture explicitly forbids an arbitrary backend
`httpx.get(result_url)` fetch of a search result (see CLAUDE.md's "OUT OF
SCOPE" list and the H1 task's own PHASE 3 note); this module answers a
narrower, structural question instead: does this URL even *look like* the
kind of public web resource H2 is allowed to treat as a clickable,
`LIVE_FETCH`-eligible evidence source, based on its shape alone?

Two independent operations:

- `is_safe_source_url()` — a hard reject list (non-http(s), malformed,
  missing host, credentialed, localhost/.local/.internal, any IP-literal
  host, overlength). This is deliberately conservative: a source that fails
  this check is never treated as `LIVE_FETCH`-eligible, full stop.
- `canonicalize_url()` — deterministic normalization for source-identity
  comparison (H1 Phase 10): lowercase scheme/host, default-port removal,
  fragment removal, tracking-parameter stripping, sorted remaining query
  params, trailing-slash normalization. Returns `None` for anything
  `is_safe_source_url()` already rejects.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_URL_LENGTH = 2048

_ALLOWED_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost")
_BLOCKED_HOSTS_EXACT = {"localhost"}
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_EXACT = {"gclid", "fbclid", "ref"}


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def is_safe_source_url(raw: str | None) -> bool:
    """Structural safety gate — no network resolution, ever."""
    if not raw or len(raw) > MAX_URL_LENGTH:
        return False
    try:
        parts = urlsplit(raw)
    except ValueError:
        return False

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    if parts.username or parts.password:
        return False

    try:
        host = parts.hostname
    except ValueError:
        return False
    if not host:
        return False

    host_l = host.lower()
    if host_l in _BLOCKED_HOSTS_EXACT:
        return False
    if any(host_l.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        return False

    # Any IP-literal host (v4 or v6, public or private/reserved) is rejected
    # structurally — a real citable evidence source is expected to resolve
    # through a hostname, not a bare address, and this also covers every
    # private/loopback/link-local/reserved case without needing DNS.
    if _parse_ip_literal(host_l) is not None:
        return False

    try:
        _ = parts.port
    except ValueError:
        return False

    return True


def canonicalize_url(raw: str | None) -> str | None:
    """Deterministic canonical form for source-identity comparison, or
    `None` if `raw` doesn't pass `is_safe_source_url()`. No network
    resolution — purely syntactic normalization."""
    if not is_safe_source_url(raw):
        return None

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port is None or port == _DEFAULT_PORTS.get(scheme) else f"{host}:{port}"

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"

    query_pairs = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower().startswith(_TRACKING_PARAM_PREFIXES) or k.lower() in _TRACKING_PARAM_EXACT)
    )
    query = urlencode(query_pairs)

    return urlunsplit((scheme, netloc, path, query, ""))
