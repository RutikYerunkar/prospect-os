"""V2-G hard gate: does `gmail.metadata` actually permit the bounded `SENT`
reconciliation scan §3.3 depends on?

Google's own documentation says the `q` parameter on `messages.list` is not
usable under `gmail.metadata` — that's not in question. What's genuinely
unverified until this script is run against a real, consented Gmail account
is whether `labelIds`-only filtering and `format="metadata"` reads are
themselves permitted under this scope (as opposed to `gmail.readonly`),
which is exactly what the frozen plan's §3.3 bounded-scan design assumes.

This is a MANUAL, READ-ONLY, NEVER-SEND probe:
- never invoked by `make test`, CI, or any other automated path;
- requires the explicit `--i-understand-this-reads-a-real-mailbox` flag;
- makes zero write/send calls of any kind;
- requires a Gmail account ALREADY connected through the real
  `POST /api/gmail/connect` -> `GET /api/gmail/callback` flow (this script
  does not perform OAuth itself — it reads the already-encrypted refresh
  token from the real, configured `DATABASE_URL` and mints a fresh access
  token from it via `GoogleOAuthRuntime.refresh_access_token()`).

It reports THREE findings INDEPENDENTLY — a failure on one must never be
read as a verdict on the others:
  1. `users.getProfile` under `gmail.metadata`.
  2. `messages.list(userId="me", labelIds=["SENT"])` under `gmail.metadata`
     (no `q` parameter — never attempted, since that's the one already-
     documented restriction this probe isn't re-litigating).
  3. `messages.get(id=..., format="metadata",
     metadataHeaders=["Message-ID","Date"])` under `gmail.metadata`, for the
     newest id `messages.list` returned (skipped, reported as such, if the
     mailbox has no SENT messages or finding #2 itself failed).

Only STRUCTURAL, SAFE observations are ever printed: HTTP status codes,
whether a field is present (never its value), and counts. Never printed,
under any circumstance: message bodies, subjects, addresses, the refresh/
access token, or any raw header value beyond the two header NAMES
(`Message-ID`/`Date`) finding #3 itself requests — and even those are
reported as present/absent, never their contents.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from groundwork.config import settings
from groundwork.db import SessionLocal
from groundwork.providers.live.google_oauth_runtime import GoogleOAuthRuntime, google_oauth_configured
from groundwork.repositories.gmail_connection import GmailConnectionRepository
from groundwork.token_crypto import TokenEncryptionError, decrypt_refresh_token

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _print_preamble() -> None:
    print("=== Groundwork V2-G Gmail scope probe — READ-ONLY, manual, never automated ===")
    print("This makes real, read-only Gmail API calls against whatever account is")
    print("currently connected. It sends nothing and writes nothing to Gmail.")
    print()


async def _load_access_token() -> str | None:
    if not google_oauth_configured():
        print("Google OAuth is not configured (GOOGLE_CLIENT_ID/SECRET/OAUTH_REDIRECT_URI) — aborting.", file=sys.stderr)
        return None

    repo = GmailConnectionRepository(SessionLocal)
    connection = await repo.get_connection()
    if connection is None or not connection.encrypted_refresh_token:
        print(
            "No Gmail account is connected on this deployment's DATABASE_URL — "
            "connect one first via the real POST /api/gmail/connect flow.",
            file=sys.stderr,
        )
        return None

    try:
        refresh_token = decrypt_refresh_token(connection.encrypted_refresh_token, connection.key_version)
    except TokenEncryptionError as exc:
        print(f"Could not decrypt the stored refresh token: {exc}", file=sys.stderr)
        return None

    runtime = GoogleOAuthRuntime.create(settings)
    try:
        return await runtime.refresh_access_token(refresh_token=refresh_token)
    finally:
        await runtime.close()


async def _probe_get_profile(client: httpx.AsyncClient) -> None:
    print("--- Finding 1: users.getProfile under gmail.metadata ---")
    response = await client.get(f"{GMAIL_API_BASE}/profile")
    print(f"  http_status: {response.status_code}")
    if response.status_code == 200:
        body = response.json()
        print(f"  emailAddress present: {bool(body.get('emailAddress'))}")
        print("  PERMITTED under gmail.metadata.")
    else:
        print("  NOT PERMITTED (or a transient failure) — see http_status above.")
    print()


async def _probe_messages_list(client: httpx.AsyncClient) -> str | None:
    print("--- Finding 2: messages.list(labelIds=['SENT']) under gmail.metadata ---")
    print("  (no `q` parameter is ever sent — that restriction is already documented)")
    response = await client.get(f"{GMAIL_API_BASE}/messages", params={"labelIds": "SENT", "maxResults": 1})
    print(f"  http_status: {response.status_code}")
    newest_id: str | None = None
    if response.status_code == 200:
        body = response.json()
        messages = body.get("messages") or []
        print(f"  messages returned: {len(messages)}")
        if messages and isinstance(messages[0], dict):
            newest_id = messages[0].get("id")
        print("  PERMITTED under gmail.metadata.")
    else:
        print("  NOT PERMITTED (or a transient failure) — see http_status above.")
    print()
    return newest_id


async def _probe_messages_get(client: httpx.AsyncClient, message_id: str | None) -> None:
    print("--- Finding 3: messages.get(format='metadata', metadataHeaders=[Message-ID,Date]) under gmail.metadata ---")
    if message_id is None:
        print("  SKIPPED — finding 2 returned no message id to look up (empty SENT, or finding 2 itself failed).")
        print()
        return
    response = await client.get(
        f"{GMAIL_API_BASE}/messages/{message_id}",
        params={"format": "metadata", "metadataHeaders": ["Message-ID", "Date"]},
    )
    print(f"  http_status: {response.status_code}")
    if response.status_code == 200:
        body = response.json()
        headers = (body.get("payload") or {}).get("headers") or []
        header_names_present = sorted({h.get("name") for h in headers if isinstance(h, dict)})
        print(f"  header NAMES present (values never printed): {header_names_present}")
        print("  PERMITTED under gmail.metadata.")
    else:
        print("  NOT PERMITTED (or a transient failure) — see http_status above.")
    print()


async def main() -> int:
    _print_preamble()
    access_token = await _load_access_token()
    if access_token is None:
        return 1

    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {access_token}"}) as client:
        await _probe_get_profile(client)
        newest_id = await _probe_messages_list(client)
        await _probe_messages_get(client, newest_id)

    print("Record these three findings in docs/PROGRESS.md — do not infer success from")
    print("documentation alone, and do not treat one finding's outcome as proof of another's.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--i-understand-this-reads-a-real-mailbox",
        action="store_true",
        dest="confirmed",
        help="required — this makes real, read-only Gmail API calls against whatever account is "
        "currently connected on this deployment.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.confirmed:
        print("Refusing to run without --i-understand-this-reads-a-real-mailbox.", file=sys.stderr)
        sys.exit(1)
    sys.exit(asyncio.run(main()))
