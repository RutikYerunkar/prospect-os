"""Structured logging — Checkpoint I1 Phase 9C.

stdlib `logging`/`dictConfig` only — no Sentry, Datadog, Langfuse, or an
OpenTelemetry exporter. `configure_logging()` is called once, at process
startup (`main.py`, before the FastAPI app is constructed), and every
subsequent `logging.getLogger(__name__).info(...)` call anywhere in the
codebase goes through the JSON formatter below automatically — nothing
else needs to change to get structured output.

Every record's rendered message (and any exception traceback attached to
it) is passed through `observability/redact.py`'s `redact()` before being
emitted — the same choke point `agent_tasks.error_message`/`runs.error`/
`llm_calls.error_message` already route through. This is a safety net, not
a substitute for redacting before logging: a call site that logs a raw
provider exception text still gets it scrubbed here, so a forgotten
`redact()` upstream doesn't leak a secret into host logs. Never logs:
prompts, source bodies/excerpts, API keys, the operator passphrase, session
cookie contents, or provider secrets — by construction (nothing in this
codebase logs those fields directly) and by this net (anything
secret-shaped that slips through anyway gets scrubbed).
"""

from __future__ import annotations

import json
import logging
import logging.config
from datetime import datetime, timezone

from groundwork.config import settings
from groundwork.observability.redact import redact

# Populated onto a LogRecord via `extra={...}` at call sites that have this
# context available (request handling, the run lifecycle, provider calls).
# Only included in the JSON payload when actually present on the record —
# never emitted as a literal `null`.
_CONTEXTUAL_FIELDS = ("request_id", "run_id", "prospect_id", "executor_id", "latency_ms")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()) or "",
            "environment": settings.environment,
        }
        for field in _CONTEXTUAL_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Idempotent — safe to call more than once (dictConfig replaces the
    prior configuration wholesale rather than stacking handlers)."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "json"},
            },
            "root": {"level": settings.log_level, "handlers": ["console"]},
            "loggers": {
                # uvicorn's own loggers get the same JSON shape/redaction
                # rather than their default plain-text formatter, and don't
                # double-log by also propagating to root.
                "uvicorn": {"level": settings.log_level, "handlers": ["console"], "propagate": False},
                "uvicorn.error": {"level": settings.log_level, "handlers": ["console"], "propagate": False},
                "uvicorn.access": {"level": settings.log_level, "handlers": ["console"], "propagate": False},
            },
        }
    )
