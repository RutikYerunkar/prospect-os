"""Mode -> `EmailSendProvider` (V2-H, Critical Decision D2).

`ProviderBundle` (`providers/base.py`) stays exactly its existing research/
search/enrichment shape — Gmail is deployment-scoped, not run-scoped (see
`providers/live/google_oauth_runtime.py`'s module docstring), so a send
provider is never added as a fourth `ProviderBundle` field. This module is
the deliberate divergence from the frozen plan's literal Part 9 wording,
recorded in `docs/PROGRESS.md`/`docs/ARCHITECTURE.md`: a separate,
mode-keyed resolver, called only at the two moments the frozen design
actually needs a send identity/provider (proposal creation's sender capture
and execute-time dispatch) — never threaded through the pipeline's own
provider wiring.

`resolve_send_provider(Mode.LIVE)` unconditionally raised
`LiveExternalEmailSendDisabled` through V2-H/V2-I-a — see that exception's
docstring in `providers/send_base.py` for the full disposition.
`LINKEDIN_COPY_AND_OPEN` must never call this resolver at all (there is no
sender identity and no executor for that action type — D6).

V2-I-b, refusal-removal gate: `GmailSendProvider` (`providers/live/
gmail_send.py`) exists now, but a working instance requires async DB access
(the connected account's decrypted refresh token via
`GmailConnectionRepository`) that this synchronous, mode-keyed function
structurally cannot provide. Real Live dispatch therefore goes through a
DIFFERENT seam entirely — `api/gmail_provider_factory.py::
build_gmail_send_provider` (async) + `api/live_send_orchestration.py::
dispatch_live_email_send`, called directly by `api/routers/actions.py::
execute_action` — never through this function. `resolve_send_provider`
itself stays Demo-only from here on; `Mode.LIVE` continues to raise
`LiveExternalEmailSendDisabled` defensively (so a stray/legacy call site
fails loudly rather than silently returning a wrong provider), but no
application code calls it that way any more.
"""

from __future__ import annotations

from groundwork.models.enums import Mode
from groundwork.providers.send_base import DemoEmailSendProvider, EmailSendProvider, LiveExternalEmailSendDisabled


def resolve_send_provider(mode: Mode) -> EmailSendProvider:
    """`Mode.DEMO` -> a fresh `DemoEmailSendProvider` (zero-egress, stateless
    — cheap to construct per call, exactly like `DemoLLMProvider`/
    `DemoSearchProvider` are constructed per run rather than cached).
    `Mode.LIVE` -> always raises `LiveExternalEmailSendDisabled` — kept as a
    defensive guard for this function specifically (see module docstring);
    the real Live dispatch path no longer calls this function at all."""
    if mode is Mode.DEMO:
        return DemoEmailSendProvider()
    raise LiveExternalEmailSendDisabled()
