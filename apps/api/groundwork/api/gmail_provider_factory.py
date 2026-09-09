"""Builds a `GmailSendProvider` from already-resolved connection state
(V2-I-b). The one place `api/routers/actions.py` (the reconcile/recover
endpoints, and — only after the refusal-removal gate — execute-time
dispatch) turns a `GmailConnectionRow` + `GoogleOAuthRuntime` into a
send-capable provider. Decrypts the refresh token once, in memory, and
passes it straight into the provider constructor — never logs it, never
persists it beyond this call.
"""

from __future__ import annotations

from groundwork.config import settings
from groundwork.providers.live.gmail_send import GmailSendProvider
from groundwork.providers.live.google_oauth_runtime import GoogleOAuthRuntime
from groundwork.repositories.gmail_connection import GmailConnectionRepository
from groundwork.token_crypto import TokenEncryptionError, decrypt_refresh_token


async def build_gmail_send_provider(
    gmail: GmailConnectionRepository, oauth_runtime: GoogleOAuthRuntime | None
) -> GmailSendProvider | None:
    """`None` whenever a real provider cannot be constructed — Gmail OAuth
    not configured on this deployment, no connection, or a refresh token
    that fails to decrypt (e.g. after a key rotation without `_OLD` set).
    Never raises; the caller maps `None` to an honest 422/409, never a
    silent Demo fallback (there is no fixture fallback in Live, ever)."""
    if oauth_runtime is None:
        return None
    connection = await gmail.get_connection()
    if connection is None or not connection.google_account_email or not connection.encrypted_refresh_token:
        return None
    try:
        refresh_token = decrypt_refresh_token(connection.encrypted_refresh_token, connection.key_version)
    except TokenEncryptionError:
        return None
    return GmailSendProvider(
        client=oauth_runtime.client,
        oauth_runtime=oauth_runtime,
        refresh_token=refresh_token,
        connected_account_email=connection.google_account_email,
        call_deadline_s=settings.gmail_send_call_deadline_s,
    )
