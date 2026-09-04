"""`contact_enrichments` / `enrichment_calls` / `contact_channels` persistence
(v2 §3.6/§H/§K) — the enrichment-side analogue of `repositories/search.py`.

Two entry points, mirroring the frozen last-known-good algorithm exactly:

- `record_success()` — a SUCCESSFUL provider call (matched or an explicit
  not-found; both are observations): insert the `enrichment_calls` attempt
  row(s), insert the immutable `contact_enrichments` observation row, derive
  both channels' states via the pure `domain/contact_identity.py` functions,
  and upsert `contact_channels` to the newly derived state — UNLESS this
  successful call's own observation for a channel is legitimately empty
  (no email address / no LinkedIn URL) AND that channel already carries a
  real, previously observed identifier: a later successful-but-empty call
  must never destroy a prior real identifier (§3.6, the V2-DH fix below) —
  only a later successful call that itself found something may replace it.
- `record_failure()` — a FAILED provider call: insert the `enrichment_calls`
  attempt row(s) only. If a channel already carries a provider-backed state,
  touch ONLY its `last_attempt_*` columns (the identifier, the three state
  columns, `observed_at` and `derived_from_enrichment_id` are untouched). If
  no provider-backed state has ever existed for that channel, derive
  `PROVIDER_ERROR` (email) / `PROVIDER_ERROR` (LinkedIn) instead.

**V2-DH fix (provider-neutral):** prior to this fix, `_upsert_success_channel`
unconditionally overwrote a channel's identifier/state/observed_at on every
SUCCESSFUL call, including one whose own observation was empty — so a
provider call that legitimately found nothing could silently erase a
previously observed real email/LinkedIn identifier. The fix: a successful
call with `identifier is None` never overwrites an existing row that already
carries a real `identifier` — only its `last_attempt_*` columns move, exactly
like a failed call's last-known-good treatment. This applies identically to
Apollo, Hunter, Demo, and any future provider — nothing here reads a
provider's name.

`derived_from_enrichment_id` (`contact_enrichments -> contact_channels`) is a
raw FK column with no ORM `relationship()` anywhere in this codebase, so the
`add -> flush() -> add` ordering matters under `PRAGMA foreign_keys=ON` —
see `repositories/llm_calls.py::create_play_with_attempts`'s docstring for
the full explanation of why.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy import select

from groundwork.domain.contact_identity import (
    IDENTITY_MATCH_VERSION,
    derive_email_channel,
    derive_linkedin_channel,
    email_discovery_state_after_failed_call,
    linkedin_resolution_state_after_failed_call,
)
from groundwork.models.enums import (
    Channel,
    EmailDiscoveryState,
    EmailVerificationState,
    EnrichmentAttemptStatus,
    LinkedInResolutionState,
)
from groundwork.models.schemas import ContactEnrichment
from groundwork.models.tables import ContactChannelRow, ContactEnrichmentRow, EnrichmentCallRow
from groundwork.observability.redact import redact
from groundwork.providers.contact_base import EnrichmentAttemptTelemetry, PersonEnrichmentResult

_NOT_ATTEMPTED_STATES = (None, "NOT_ATTEMPTED")


class ContactEnrichmentRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def _telemetry_rows(
        self, *, telemetry: list[EnrichmentAttemptTelemetry], call_group_id: str,
        run_id: str, prospect_id: str, provider: str,
    ) -> list[EnrichmentCallRow]:
        return [
            EnrichmentCallRow(
                id=str(uuid.uuid4()),
                call_group_id=call_group_id,
                attempt=t.attempt,
                attempt_kind=t.attempt_kind.value,
                operation=t.operation.value,
                run_id=run_id,
                prospect_id=prospect_id,
                provider=provider,
                status=t.status.value,
                started_at=t.started_at,
                finished_at=t.finished_at,
                latency_ms=t.latency_ms,
                http_status=t.http_status,
                provider_request_id=t.provider_request_id,
                error_type=t.error_type,
                error_message=redact(t.error_message),
                cost_usd=t.cost_usd,
                credits_used=t.credits_used,
                input_digest=t.input_digest,
                output_digest=t.output_digest,
            )
            for t in telemetry
        ]

    async def record_success(
        self,
        *,
        run_id: str,
        prospect_id: str,
        provider: str,
        call_group_id: str,
        telemetry: list[EnrichmentAttemptTelemetry],
        result: PersonEnrichmentResult,
        email_status_map: Mapping[str, EmailVerificationState],
        grounded_full_name: str | None,
        grounded_company_name: str | None,
        grounded_company_domain: str | None,
    ) -> None:
        async with self._session_factory() as session:
            for row in self._telemetry_rows(
                telemetry=telemetry, call_group_id=call_group_id, run_id=run_id,
                prospect_id=prospect_id, provider=provider,
            ):
                session.add(row)
            await session.flush()

            observed_at = (
                (result.email.observed_at if result.email else None)
                or (result.linkedin.observed_at if result.linkedin else None)
                or datetime.now(timezone.utc)
            )

            # Re-validated through the Pydantic schema — its model validators
            # enforce the origin-bound LinkedIn identifier grammar a SECOND
            # time, independent of `domain/contact_identity.py`'s own check
            # (§H — "secrets are scrubbed twice, not once").
            enrichment_model = ContactEnrichment(
                prospect_id=prospect_id,
                provider=provider,
                call_group_id=call_group_id,
                matched=result.matched,
                origin=result.origin,
                observed_at=observed_at,
                raw_digest=result.raw_digest,
                provider_person_id=result.provider_person_id,
                email_address=result.email.address if result.email else None,
                email_provider_status=result.email.provider_status if result.email else None,
                email_provider_confidence=result.email.provider_confidence if result.email else None,
                email_is_catch_all=result.email.is_catch_all if result.email else None,
                linkedin_url=result.linkedin.profile_url if result.linkedin else None,
                linkedin_asserted_full_name=result.linkedin.asserted_full_name if result.linkedin else None,
                linkedin_asserted_company_name=result.linkedin.asserted_company_name if result.linkedin else None,
                linkedin_asserted_company_domain=result.linkedin.asserted_company_domain if result.linkedin else None,
                linkedin_asserted_title=result.linkedin.asserted_title if result.linkedin else None,
            )

            enrichment_id = str(uuid.uuid4())
            session.add(
                ContactEnrichmentRow(
                    id=enrichment_id,
                    prospect_id=enrichment_model.prospect_id,
                    provider=enrichment_model.provider,
                    call_group_id=enrichment_model.call_group_id,
                    matched=enrichment_model.matched,
                    origin=enrichment_model.origin.value,
                    observed_at=enrichment_model.observed_at,
                    raw_digest=enrichment_model.raw_digest,
                    provider_person_id=enrichment_model.provider_person_id,
                    email_address=enrichment_model.email_address,
                    email_provider_status=enrichment_model.email_provider_status,
                    email_provider_confidence=enrichment_model.email_provider_confidence,
                    email_is_catch_all=enrichment_model.email_is_catch_all,
                    linkedin_url=enrichment_model.linkedin_url,
                    linkedin_asserted_full_name=enrichment_model.linkedin_asserted_full_name,
                    linkedin_asserted_company_name=enrichment_model.linkedin_asserted_company_name,
                    linkedin_asserted_company_domain=enrichment_model.linkedin_asserted_company_domain,
                    linkedin_asserted_title=enrichment_model.linkedin_asserted_title,
                )
            )
            await session.flush()  # `contact_enrichments.id` must exist before `contact_channels` references it

            now = datetime.now(timezone.utc)

            email_discovery, email_verification = derive_email_channel(result.email, status_map=email_status_map)
            await self._upsert_success_channel(
                session, prospect_id=prospect_id, channel=Channel.EMAIL,
                identifier=result.email.address if result.email else None,
                discovery_state=email_discovery.value, verification_state=email_verification.value,
                identity_match_state=None, derived_from_enrichment_id=enrichment_id,
                observed_at=enrichment_model.observed_at, now=now,
            )

            linkedin_resolution, linkedin_identity = derive_linkedin_channel(
                result.linkedin, origin=result.origin,
                grounded_full_name=grounded_full_name,
                grounded_company_name=grounded_company_name,
                grounded_company_domain=grounded_company_domain,
            )
            await self._upsert_success_channel(
                session, prospect_id=prospect_id, channel=Channel.LINKEDIN,
                identifier=result.linkedin.profile_url if result.linkedin else None,
                discovery_state=linkedin_resolution.value, verification_state=None,
                identity_match_state=linkedin_identity.value, derived_from_enrichment_id=enrichment_id,
                observed_at=enrichment_model.observed_at, now=now,
            )

            await session.commit()

    async def record_failure(
        self,
        *,
        run_id: str,
        prospect_id: str,
        provider: str,
        call_group_id: str,
        telemetry: list[EnrichmentAttemptTelemetry],
    ) -> None:
        async with self._session_factory() as session:
            for row in self._telemetry_rows(
                telemetry=telemetry, call_group_id=call_group_id, run_id=run_id,
                prospect_id=prospect_id, provider=provider,
            ):
                session.add(row)
            await session.flush()

            now = datetime.now(timezone.utc)
            last = telemetry[-1] if telemetry else None
            last_status = last.status.value if last else EnrichmentAttemptStatus.PROVIDER_ERROR.value
            last_error_type = last.error_type if last else None

            await self._apply_failure_to_channel(
                session, prospect_id=prospect_id, channel=Channel.EMAIL,
                now=now, last_status=last_status, last_error_type=last_error_type,
            )
            await self._apply_failure_to_channel(
                session, prospect_id=prospect_id, channel=Channel.LINKEDIN,
                now=now, last_status=last_status, last_error_type=last_error_type,
            )
            await session.commit()

    async def _get_channel_row(self, session, *, prospect_id: str, channel: Channel) -> ContactChannelRow | None:
        result = await session.execute(
            select(ContactChannelRow).where(
                ContactChannelRow.prospect_id == prospect_id, ContactChannelRow.channel == channel.value
            )
        )
        return result.scalar_one_or_none()

    async def _upsert_success_channel(
        self, session, *, prospect_id: str, channel: Channel, identifier: str | None,
        discovery_state: str, verification_state: str | None, identity_match_state: str | None,
        derived_from_enrichment_id: str, observed_at: datetime, now: datetime,
    ) -> None:
        row = await self._get_channel_row(session, prospect_id=prospect_id, channel=channel)

        # §3.6 last-known-good, successful-but-empty fix: THIS call's own
        # observation is empty (no email address / no LinkedIn URL). If a
        # real identifier was already on record, a legitimately-empty
        # SUCCESSFUL call must never erase it — only a later successful call
        # that itself found something may replace it. Touch ONLY the
        # attempt-telemetry columns here, exactly like a failed call.
        if identifier is None and row is not None and row.identifier is not None:
            row.last_attempt_at = now
            row.last_attempt_status = "OK"
            row.last_attempt_error_type = None
            return

        if row is None:
            session.add(
                ContactChannelRow(
                    id=str(uuid.uuid4()), prospect_id=prospect_id, channel=channel.value,
                    identifier=identifier, discovery_state=discovery_state, verification_state=verification_state,
                    identity_match_state=identity_match_state, derivation_version=IDENTITY_MATCH_VERSION,
                    derived_from_enrichment_id=derived_from_enrichment_id, observed_at=observed_at,
                    last_attempt_at=now, last_attempt_status="OK", last_attempt_error_type=None,
                )
            )
            return
        row.identifier = identifier
        row.discovery_state = discovery_state
        row.verification_state = verification_state
        row.identity_match_state = identity_match_state
        row.derivation_version = IDENTITY_MATCH_VERSION
        row.derived_from_enrichment_id = derived_from_enrichment_id
        row.observed_at = observed_at
        row.last_attempt_at = now
        row.last_attempt_status = "OK"
        row.last_attempt_error_type = None

    async def _apply_failure_to_channel(
        self, session, *, prospect_id: str, channel: Channel, now: datetime,
        last_status: str, last_error_type: str | None,
    ) -> None:
        row = await self._get_channel_row(session, prospect_id=prospect_id, channel=channel)

        if row is not None and row.discovery_state not in _NOT_ATTEMPTED_STATES:
            # §3.6 last-known-good: a failed call never overwrites a
            # previously derived, provider-backed state — touch ONLY the
            # attempt-telemetry columns.
            row.last_attempt_at = now
            row.last_attempt_status = last_status
            row.last_attempt_error_type = last_error_type
            return

        if channel is Channel.EMAIL:
            existing = EmailDiscoveryState(row.discovery_state) if row and row.discovery_state else None
            new_state = email_discovery_state_after_failed_call(existing).value
        else:
            existing_li = LinkedInResolutionState(row.discovery_state) if row and row.discovery_state else None
            new_state = linkedin_resolution_state_after_failed_call(existing_li).value

        if row is None:
            session.add(
                ContactChannelRow(
                    id=str(uuid.uuid4()), prospect_id=prospect_id, channel=channel.value,
                    identifier=None, discovery_state=new_state, verification_state=None, identity_match_state=None,
                    derivation_version=IDENTITY_MATCH_VERSION, derived_from_enrichment_id=None, observed_at=None,
                    last_attempt_at=now, last_attempt_status=last_status, last_attempt_error_type=last_error_type,
                )
            )
            return
        row.discovery_state = new_state
        row.identifier = None
        row.verification_state = None
        row.identity_match_state = None
        row.last_attempt_at = now
        row.last_attempt_status = last_status
        row.last_attempt_error_type = last_error_type

    # --- reads (API aggregate) ---

    async def get_contact_channels(self, prospect_id: str) -> list[ContactChannelRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ContactChannelRow).where(ContactChannelRow.prospect_id == prospect_id)
            )
            return list(result.scalars())

    async def get_contact_enrichments(self, prospect_id: str) -> list[ContactEnrichmentRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ContactEnrichmentRow).where(ContactEnrichmentRow.prospect_id == prospect_id)
            )
            return list(result.scalars())

    async def get_enrichment_calls(self, prospect_id: str) -> list[EnrichmentCallRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(EnrichmentCallRow).where(EnrichmentCallRow.prospect_id == prospect_id)
            )
            return list(result.scalars())
