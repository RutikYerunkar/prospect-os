"""`gmail_connections` / `oauth_states` — V2-G. Exactly one connection row
(`id="default"`) and short-lived, single-use OAuth state rows.

`consume_state()` is the load-bearing method: a single guarded
`UPDATE ... WHERE state=:id AND consumed_at IS NULL AND expires_at > :now`,
mirroring `repositories/runs.py`'s ownership-guarded lease pattern exactly.
A `rowcount` of anything other than 1 (already consumed, never existed, or
expired) means the caller must treat this as a conflict/replay and MUST NOT
proceed to a token exchange — there is deliberately no read-then-write here.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, update

from groundwork.domain.contact_identity import normalize_email_identity
from groundwork.models.tables import GmailConnectionRow, OAuthStateRow
from groundwork.timeutil import utcnow

CONNECTION_ID = "default"


class GmailConnectionRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    # --- oauth_states -------------------------------------------------

    async def create_state(self, *, state_id: str, pkce_verifier: str, ttl_s: float) -> None:
        async with self._session_factory() as session:
            now = utcnow()
            session.add(
                OAuthStateRow(
                    state=state_id,
                    pkce_verifier=pkce_verifier,
                    created_at=now,
                    expires_at=now + timedelta(seconds=ttl_s),
                )
            )
            await session.commit()

    async def consume_state(self, state_id: str) -> OAuthStateRow | None:
        """The one guarded UPDATE (§Callback order step 4). Returns the row
        (carrying `pkce_verifier`) iff exactly one row was consumed by THIS
        call; `None` for a replay, an unknown state, or an expired one —
        the caller must map that to `409 Conflict` and perform no exchange."""
        async with self._session_factory() as session:
            now = utcnow()
            result = await session.execute(
                update(OAuthStateRow)
                .where(
                    OAuthStateRow.state == state_id,
                    OAuthStateRow.consumed_at.is_(None),
                    OAuthStateRow.expires_at > now,
                )
                .values(consumed_at=now)
            )
            if result.rowcount != 1:
                await session.rollback()
                return None
            row = await session.get(OAuthStateRow, state_id)
            await session.commit()
            return row

    async def delete_expired_states(self, *, before: datetime) -> int:
        """Opportunistic cleanup, called from `POST /api/gmail/connect` —
        never load-bearing for correctness (an expired-but-not-yet-deleted
        row is already rejected by `consume_state`'s own `expires_at`
        check), just housekeeping."""
        async with self._session_factory() as session:
            result = await session.execute(delete(OAuthStateRow).where(OAuthStateRow.expires_at < before))
            await session.commit()
            return result.rowcount

    # --- gmail_connections ---------------------------------------------

    async def get_connection(self) -> GmailConnectionRow | None:
        async with self._session_factory() as session:
            return await session.get(GmailConnectionRow, CONNECTION_ID)

    async def upsert_connection(
        self,
        *,
        google_account_email: str,
        encrypted_refresh_token: str,
        key_version: int,
        scopes: list[str],
        connected_at: datetime,
        connected_by_actor: str,
        last_refreshed_at: datetime,
    ) -> GmailConnectionRow:
        """Singleton row, last-writer-wins (§Retries/concurrency) — two
        concurrent connect attempts each own separate `oauth_states` rows,
        but only one `gmail_connections` row ever exists."""
        async with self._session_factory() as session:
            row = await session.get(GmailConnectionRow, CONNECTION_ID)
            if row is None:
                row = GmailConnectionRow(id=CONNECTION_ID)
                session.add(row)
            row.google_account_email = google_account_email
            row.encrypted_refresh_token = encrypted_refresh_token
            row.key_version = key_version
            row.scopes = scopes
            row.connected_at = connected_at
            row.connected_by_actor = connected_by_actor
            row.last_refreshed_at = last_refreshed_at
            row.revoked_at = None
            await session.commit()
            await session.refresh(row)
            return row

    async def connected_account_identifier(self) -> str | None:
        """The identity that WOULD send — an email address, canonicalized
        via the same `normalize_email_identity` the future send-policy/hash
        machinery uses (§3.8/§3.10), never a credential. `None` when
        nothing is connected (before the first connect, and again after
        disconnect — `get_connection()` returns `None` either way, since
        disconnect deletes the row outright)."""
        row = await self.get_connection()
        if row is None or not row.google_account_email:
            return None
        return normalize_email_identity(row.google_account_email)

    async def delete_connection(self) -> bool:
        """Disconnect deletes the row outright — there is no "revoked but
        still present" state; `GET /api/gmail/connection` reads absence as
        `connected: false`."""
        async with self._session_factory() as session:
            result = await session.execute(delete(GmailConnectionRow).where(GmailConnectionRow.id == CONNECTION_ID))
            await session.commit()
            return result.rowcount == 1
