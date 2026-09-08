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

`resolve_send_provider(Mode.LIVE)` unconditionally raises
`LiveExternalEmailSendDisabled` — see that exception's docstring in
`providers/send_base.py` for the full disposition. `LINKEDIN_COPY_AND_OPEN`
must never call this resolver at all (there is no sender identity and no
executor for that action type — D6).

V2-I-b status (gate-order correction — see `docs/PROGRESS.md`):
`GmailSendProvider` (`providers/live/gmail_send.py`) and the real async
dispatch seam (`api/gmail_provider_factory.py::build_gmail_send_provider` +
`api/live_send_orchestration.py::dispatch_live_email_send`) are fully
implemented and independently unit-tested. `api/routers/actions.py::
execute_action`'s `LIVE_EXTERNAL` branch calls `resolve_send_provider
(Mode.LIVE)` FIRST — this function's unconditional raise — BEFORE that async
seam is ever reached, so real Live dispatch stays unreachable regardless of
whether a working Gmail connection exists. This refusal is removed only in
a dedicated, separately-reviewed step, once the accepted plan's full
verification checklist (including Postgres + migration drift, run in CI)
has actually passed — never as a side effect of implementing, wiring, or
configuring anything.
"""

from __future__ import annotations

from groundwork.models.enums import Mode
from groundwork.providers.send_base import DemoEmailSendProvider, EmailSendProvider, LiveExternalEmailSendDisabled


def resolve_send_provider(mode: Mode) -> EmailSendProvider:
    """`Mode.DEMO` -> a fresh `DemoEmailSendProvider` (zero-egress, stateless
    — cheap to construct per call, exactly like `DemoLLMProvider`/
    `DemoSearchProvider` are constructed per run rather than cached).
    `Mode.LIVE` -> always raises `LiveExternalEmailSendDisabled`, never
    `ProviderNotConfigured` and never conditioned on any registered
    provider/runtime — see D1/D4. This is the load-bearing gate
    `execute_action` calls BEFORE ever reaching the real (fully implemented)
    async dispatch seam — see module docstring."""
    if mode is Mode.DEMO:
        return DemoEmailSendProvider()
    raise LiveExternalEmailSendDisabled()
