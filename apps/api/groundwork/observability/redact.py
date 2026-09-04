"""Central redaction choke point (§26/Phase 11).

Every place an exception or provider error becomes a persisted string —
`agent_tasks.error_message`, `llm_calls.error_message`/`validation_error` —
must route through `redact()` first. This is the one place a leaked API key
in a provider's error text gets scrubbed before it reaches the DB, the trace
table, or any log line; it is not a UI convention.
"""

from __future__ import annotations

import re

from groundwork.config import settings

_MAX_LEN = 2000

# Generic bearer/API-key-shaped tokens, in case a provider echoes one back
# in an error message that never touches settings at all (e.g. a copied
# request header dumped into a 401 body).
_GENERIC_SECRET_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{10,}|Bearer\s+[A-Za-z0-9._-]{10,})\b")


def _configured_secrets() -> list[str]:
    secrets = []
    if settings.openai_api_key:
        secrets.append(settings.openai_api_key)
    if settings.tavily_api_key:
        secrets.append(settings.tavily_api_key)
    if settings.apollo_api_key:
        secrets.append(settings.apollo_api_key)
    if settings.hunter_api_key:
        secrets.append(settings.hunter_api_key)
    return [s for s in secrets if s]


def redact(text: str | None) -> str | None:
    """Strip any configured secret value, and any generic secret-shaped
    token, from `text`. Truncates long payloads (schema validation errors in
    particular can be huge) so a single bad row can't bloat the trace."""
    if text is None:
        return None
    redacted = text
    for secret in _configured_secrets():
        if secret in redacted:
            redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _GENERIC_SECRET_RE.sub("[REDACTED]", redacted)
    if len(redacted) > _MAX_LEN:
        redacted = redacted[:_MAX_LEN] + "...[truncated]"
    return redacted
