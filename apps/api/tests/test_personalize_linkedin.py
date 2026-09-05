"""v2 §V2-F — LinkedIn drafting in `engine/steps/personalize.py`.

Eligibility is `contact_channels[LINKEDIN].discovery_state == RESOLVED` ONLY
(identity policy is NOT duplicated here — a MISMATCH profile still gets a
draft, and `domain/review.py::_no_fabricated_contact` blocks it
deterministically afterward). The email branch must stay byte-identical to
v1 regardless of whether a LinkedIn draft is also produced.
"""

from __future__ import annotations

from datetime import date

from groundwork.engine.context import ProspectContext
from groundwork.engine.steps.personalize import personalize
from groundwork.models.enums import Channel, ContactVerification, LinkedInIdentityState, SignalType
from groundwork.models.schemas import CompanySeed, Contact, ContactChannelState, PlaySpec, Signal
from groundwork.providers.base import ProviderBundle
from groundwork.providers.demo.demo_llm import DemoLLMProvider
from groundwork.providers.demo.fixtures import load_fixture_pack


class _Noop:
    async def record(self, *a, **kw) -> None: ...
    async def has_succeeded(self, *a, **kw) -> bool:
        return False
    async def emit(self, *a, **kw) -> None: ...


def _company() -> CompanySeed:
    return CompanySeed(
        slug="acme", name="Acme Corp", domain="acme.com", industry="ai_infrastructure",
        size_band="51-200", employee_count=120,
    )


def _play_spec() -> PlaySpec:
    return PlaySpec(objective_text="t", target_industries=["ai_infrastructure"])


def _signal(eid: str) -> Signal:
    return Signal(
        id=eid, prospect_id="p-1", type=SignalType.FUNDING, summary="Acme raised a Series B",
        confidence=0.9, evidence_ids=[eid], grounded=True,
    )


def _ctx(*, contact_channels: list[ContactChannelState]) -> ProspectContext:
    providers = ProviderBundle(
        llm=DemoLLMProvider(load_fixture_pack(), seed=42),
        search=None,  # type: ignore[arg-type]
    )
    ctx = ProspectContext(
        run_id="r-1", prospect_id="p-1", company=_company(), dedupe_key="k",
        play_spec=_play_spec(), providers=providers, reference_date=date(2026, 1, 1),
        trace=_Noop(), events=_Noop(), llm_calls=_Noop(),  # type: ignore[arg-type]
        search_calls=_Noop(), enrichment_calls=_Noop(),  # type: ignore[arg-type]
    )
    ctx.contact = Contact(
        prospect_id="p-1", full_name="Jane Doe", title="VP Eng",
        verification=ContactVerification.VERIFIED, evidence_ids=["ev-1"],
    )
    ctx.signals = [_signal("ev-1")]
    ctx.contact_channels = contact_channels
    return ctx


def _linkedin_channel(discovery_state: str, identity_match_state: str | None = None) -> ContactChannelState:
    return ContactChannelState(
        channel=Channel.LINKEDIN, identifier="https://www.linkedin.com/in/jane-doe",
        discovery_state=discovery_state, identity_match_state=identity_match_state,
        derived_from_enrichment_id="enr-1",
    )


async def test_resolved_linkedin_yields_email_and_linkedin_drafts() -> None:
    ctx = _ctx(contact_channels=[_linkedin_channel("RESOLVED", LinkedInIdentityState.STRONG_MATCH.value)])
    result = await personalize(ctx)
    assert result.ok is True
    channels = {d.channel for d in ctx.drafts}
    assert channels == {Channel.EMAIL, Channel.LINKEDIN}


async def test_non_resolved_linkedin_yields_email_only() -> None:
    for state in ("NOT_ATTEMPTED", "NOT_FOUND", "PROVIDER_ERROR"):
        ctx = _ctx(contact_channels=[_linkedin_channel(state)])
        await personalize(ctx)
        channels = {d.channel for d in ctx.drafts}
        assert channels == {Channel.EMAIL}, f"unexpected LinkedIn draft for discovery_state={state}"


async def test_empty_contact_channels_yields_email_only() -> None:
    ctx = _ctx(contact_channels=[])
    await personalize(ctx)
    assert {d.channel for d in ctx.drafts} == {Channel.EMAIL}


async def test_mismatch_still_yields_a_linkedin_draft() -> None:
    """Identity policy is NOT duplicated in personalization (§V2-F decision
    2) — a MISMATCH profile that is otherwise RESOLVED still drafts; only
    `domain/review.py` blocks it, deterministically, afterward."""
    ctx = _ctx(contact_channels=[_linkedin_channel("RESOLVED", LinkedInIdentityState.MISMATCH.value)])
    await personalize(ctx)
    channels = {d.channel for d in ctx.drafts}
    assert Channel.LINKEDIN in channels


async def test_linkedin_draft_has_no_subject_and_correct_step_index() -> None:
    ctx = _ctx(contact_channels=[_linkedin_channel("RESOLVED", LinkedInIdentityState.STRONG_MATCH.value)])
    await personalize(ctx)
    linkedin_draft = next(d for d in ctx.drafts if d.channel is Channel.LINKEDIN)
    email_draft = next(d for d in ctx.drafts if d.channel is Channel.EMAIL)
    assert linkedin_draft.subject is None
    assert linkedin_draft.step_index == 1
    assert email_draft.step_index == 0
    assert linkedin_draft.content_hash is None
    assert linkedin_draft.body.strip() != ""


async def test_distinct_ctx_keys_for_email_and_linkedin_llm_calls() -> None:
    seen_ctx_keys: list[str] = []
    real_structured = DemoLLMProvider.structured

    async def _spy_structured(self, envelope, schema, *, ctx_key, operation):
        seen_ctx_keys.append(ctx_key)
        return await real_structured(self, envelope, schema, ctx_key=ctx_key, operation=operation)

    DemoLLMProvider.structured = _spy_structured  # type: ignore[assignment]
    try:
        ctx = _ctx(contact_channels=[_linkedin_channel("RESOLVED", LinkedInIdentityState.STRONG_MATCH.value)])
        await personalize(ctx)
    finally:
        DemoLLMProvider.structured = real_structured  # type: ignore[assignment]

    assert seen_ctx_keys == ["r-1:p-1:personalize", "r-1:p-1:personalize:linkedin"]


async def test_unavailable_verification_skips_both_drafts() -> None:
    ctx = _ctx(contact_channels=[_linkedin_channel("RESOLVED", LinkedInIdentityState.STRONG_MATCH.value)])
    ctx.contact = Contact(prospect_id="p-1", verification=ContactVerification.UNAVAILABLE)
    result = await personalize(ctx)
    assert result.skipped is True
    assert ctx.drafts == []
