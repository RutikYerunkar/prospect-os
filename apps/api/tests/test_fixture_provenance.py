"""No DEMO_FIXTURE evidence may ever carry an http(s) source_url — enforced
structurally by the Evidence model validator (§12), and re-asserted here
directly against the shipped fixture pack so a future fixture edit can't
regress it silently.
"""

import pytest
from pydantic import ValidationError

from groundwork.models.enums import EvidenceOrigin
from groundwork.models.schemas import Evidence
from groundwork.providers.demo.fixtures import load_fixture_pack


def test_no_fixture_source_has_an_http_url() -> None:
    pack = load_fixture_pack()
    for company in pack.companies:
        for source in company.sources:
            assert not source.snippet.startswith(("http://", "https://"))
            # The fixture schema doesn't even expose a source_url field —
            # sources only carry ref/title/claim/snippet.
            assert not hasattr(source, "source_url")


def test_every_fixture_row_has_a_title_and_snippet() -> None:
    pack = load_fixture_pack()
    for company in pack.companies:
        for source in company.sources:
            assert source.title.strip()
            assert source.snippet.strip()
            assert source.claim.strip()


def test_model_validator_rejects_fake_url_on_demo_fixture_evidence() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            id="ev-1",
            prospect_id="p-1",
            source_url="https://techcrunch.com/fake-article",
            source_provider="demo_fixture",
            title="t",
            claim="c",
            snippet="s",
            confidence=0.9,
            origin=EvidenceOrigin.DEMO_FIXTURE,
        )


def test_model_validator_rejects_fake_url_on_llm_inference_evidence() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            id="ev-1",
            prospect_id="p-1",
            source_url="https://example.com/inferred",
            source_provider="demo_llm",
            title="t",
            claim="c",
            snippet="s",
            confidence=0.5,
            origin=EvidenceOrigin.LLM_INFERENCE,
        )


def test_model_validator_requires_http_url_on_live_fetch_evidence() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            id="ev-1",
            prospect_id="p-1",
            source_url=None,
            source_provider="tavily",
            title="t",
            claim="c",
            snippet="s",
            confidence=0.9,
            origin=EvidenceOrigin.LIVE_FETCH,
        )


def test_demo_fixture_evidence_with_no_url_is_valid() -> None:
    evidence = Evidence(
        id="ev-1",
        prospect_id="p-1",
        source_ref="demo://fixtures/northwind-labs/funding-note",
        source_provider="demo_fixture",
        title="t",
        claim="c",
        snippet="s",
        confidence=0.9,
        origin=EvidenceOrigin.DEMO_FIXTURE,
    )
    assert evidence.source_url is None
