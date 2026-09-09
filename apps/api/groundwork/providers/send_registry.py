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

`resolve_send_provider(Mode.LIVE)` still unconditionally raises
`LiveExternalEmailSendDisabled` — see that exception's docstring in
`providers/send_base.py` for the full disposition — but it is kept as a
defensive-only guard for this function specifically. `LINKEDIN_COPY_AND_OPEN`
must never call this resolver at all (there is no sender identity and no
executor for that action type — D6).

V2-I-b status (final — see `docs/PROGRESS.md` and PR #23): real Live
`EMAIL_SEND` dispatch IS reachable now. `GmailSendProvider` (`providers/
live/gmail_send.py`) and the real async dispatch seam
(`api/gmail_provider_factory.py::build_gmail_send_provider` +
`api/live_send_orchestration.py::dispatch_live_email_send`) go through a
DIFFERENT path than this function — a synchronous, mode-keyed resolver
structurally cannot provide the async DB access (the connected account's
decrypted refresh token) a real Live provider needs. `api/routers/
actions.py::execute_action`'s `LIVE_EXTERNAL` branch calls the async seam
directly and never calls this function with `Mode.LIVE`. The removal of
this function from that dispatch path happened only after the accepted
plan's full verification checklist actually passed in CI (full SQLite,
full Postgres + migration drift, canonical Demo, the complete safety-test
matrix) and on the user's explicit, separate authorization.
"""

from __future__ import annotations

from groundwork.models.enums import Mode
from groundwork.providers.send_base import DemoEmailSendProvider, EmailSendProvider, LiveExternalEmailSendDisabled


def resolve_send_provider(mode: Mode) -> EmailSendProvider:
    """`Mode.DEMO` -> a fresh `DemoEmailSendProvider` (zero-egress, stateless
    — cheap to construct per call, exactly like `DemoLLMProvider`/
    `DemoSearchProvider` are constructed per run rather than cached).
    `Mode.LIVE` -> always raises `LiveExternalEmailSendDisabled` — a
    defensive guard only; real Live dispatch no longer calls this function
    at all (see module docstring)."""
    if mode is Mode.DEMO:
        return DemoEmailSendProvider()
    raise LiveExternalEmailSendDisabled()
