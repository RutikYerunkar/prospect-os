"""v2.0.2 retrieval alignment — RC-1 starvation regression + fix.

RC-1: the pre-v2.0.2 `TavilySearchProvider.fetch_sources()` issued a
company's category queries in fixed order and applied the global occurrence
ceiling *during* issuance (breaking out of the loop once the ceiling was
hit). Whenever an early category alone returned enough results to fill the
ceiling, every later category was starved out entirely — even though the
run's search-call budget had room to spare and the later category's own
query had not even been degraded or budget-blocked.

This module first reproduces that regression against a `_naive_sequential_
fill()` helper standing in for the removed pre-fix behavior (so the bug is
demonstrated locally, not just asserted away), then proves the shipped
`_allocate_balanced_occurrences()`/`_category_balanced_extract_order()`
fix it with deterministic round-robin allocation. No real provider calls
happen anywhere in this module.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

import httpx

from groundwork.domain.query_plan import QueryTemplateId
from groundwork.models.schemas import CompanySeed, PlaySpec, SourceDocument
from groundwork.providers.live.tavily_search import (
    _allocate_balanced_occurrences,
    _category_balanced_extract_order,
)
from tests.search_live_helpers import extract_response, make_search_provider, search_response, search_result

_FUNDING = QueryTemplateId.COMPANY_FUNDING
_CAREERS = QueryTemplateId.COMPANY_CAREERS
_SIZE = QueryTemplateId.COMPANY_SIZE
_CATEGORY_ORDER = [_FUNDING, _CAREERS, _SIZE]


def _doc(category: QueryTemplateId, index: int, *, url: str | None = None, text: str = "x") -> SourceDocument:
    return SourceDocument(
        ref=f"src:{category.value}:{index}",
        title=f"{category.value} result {index}",
        text=text,
        source_provider="tavily",
        url=url or f"https://acme.example.com/{category.value}/{index}",
        canonical_url=url or f"https://acme.example.com/{category.value}/{index}",
        rank=index,
        retrieved_at=datetime.now(timezone.utc),
    )


def _naive_sequential_fill(
    category_order: list[QueryTemplateId],
    hits_by_category: dict[QueryTemplateId, list[SourceDocument]],
    *,
    ceiling: int,
) -> list[SourceDocument]:
    """Stand-in for the removed pre-v2.0.2 behavior: drain categories
    strictly in fixed order, one category fully before the next, until the
    ceiling is hit. This is RC-1's actual bug — kept here only to prove the
    regression this checkpoint fixes, never imported by production code."""
    allocated: list[SourceDocument] = []
    for tid in category_order:
        if len(allocated) >= ceiling:
            break
        remaining = ceiling - len(allocated)
        allocated.extend(hits_by_category.get(tid, [])[:remaining])
    return allocated


def _hits_by_category(per_category_count: int) -> dict[QueryTemplateId, list[SourceDocument]]:
    return {tid: [_doc(tid, i) for i in range(per_category_count)] for tid in _CATEGORY_ORDER}


# --- RC-1 regression, established against the pre-fix (naive) behavior -----


def test_rc1_starvation_regression_reproduced_by_naive_sequential_fill() -> None:
    """Establishes the regression locally: 3 categories each return more
    hits than fit under the ceiling; naive sequential fill lets `funding`
    alone consume the entire ceiling, starving `careers` and `size` out
    completely even though both had 10 real hits waiting."""
    hits = _hits_by_category(10)
    naive = _naive_sequential_fill(_CATEGORY_ORDER, hits, ceiling=15)
    assert len(naive) == 15
    assert all(doc.ref.startswith("src:company_funding:") for doc in naive[:10])
    assert all(doc.ref.startswith("src:company_careers:") for doc in naive[10:15])
    # The bug: `company_size` gets zero occurrences despite having 10 real
    # hits ready — this is RC-1.
    assert not any(doc.ref.startswith("src:company_size:") for doc in naive)


def test_rc1_fix_balanced_allocation_gives_every_category_a_share() -> None:
    """Same input as the regression test above, through the shipped fix:
    every category with hits gets a non-zero, evenly-divided share."""
    hits = _hits_by_category(10)
    allocated, category_by_ref = _allocate_balanced_occurrences(_CATEGORY_ORDER, hits, ceiling=15)
    assert len(allocated) == 15
    counts = Counter(category_by_ref[doc.ref] for doc in allocated)
    assert counts == {_FUNDING: 5, _CAREERS: 5, _SIZE: 5}


def test_balanced_allocation_uneven_category_volume_still_deterministic_round_robin() -> None:
    """`size` only has 2 real hits (e.g. a thin result set) — round robin
    must still give `funding`/`careers` their fair remaining share rather
    than stopping early, and never invent a hit `size` doesn't have."""
    hits = {
        _FUNDING: [_doc(_FUNDING, i) for i in range(10)],
        _CAREERS: [_doc(_CAREERS, i) for i in range(10)],
        _SIZE: [_doc(_SIZE, i) for i in range(2)],
    }
    allocated, category_by_ref = _allocate_balanced_occurrences(_CATEGORY_ORDER, hits, ceiling=15)
    assert len(allocated) == 15
    counts = Counter(category_by_ref[doc.ref] for doc in allocated)
    assert counts[_SIZE] == 2  # exhausted, never fabricated
    assert counts[_FUNDING] == 7 and counts[_CAREERS] == 6  # remaining 13 split fairly, funding (first) rounds up


def test_balanced_allocation_is_deterministic_across_repeated_calls() -> None:
    hits = _hits_by_category(10)
    first, first_map = _allocate_balanced_occurrences(_CATEGORY_ORDER, hits, ceiling=15)
    second, second_map = _allocate_balanced_occurrences(_CATEGORY_ORDER, hits, ceiling=15)
    assert [d.ref for d in first] == [d.ref for d in second]
    assert first_map == second_map


def test_balanced_allocation_respects_global_ceiling_of_15() -> None:
    hits = _hits_by_category(20)
    allocated, _ = _allocate_balanced_occurrences(_CATEGORY_ORDER, hits, ceiling=15)
    assert len(allocated) == 15


def test_balanced_allocation_stops_when_all_categories_exhausted_below_ceiling() -> None:
    hits = {tid: [_doc(tid, i) for i in range(2)] for tid in _CATEGORY_ORDER}
    allocated, _ = _allocate_balanced_occurrences(_CATEGORY_ORDER, hits, ceiling=15)
    assert len(allocated) == 6  # 2 per category, never padded to 15


# --- category-balanced extract ordering (distinct from occurrence balance) -


def test_category_balanced_extract_order_survives_a_winner_dedup_collision() -> None:
    """A URL returned under two categories collapses to one winner
    (`select_winners`'s job, exercised at the fetch_sources level below) —
    this test proves the *ordering* step alone still balances what's left
    across categories, rather than a plain first-seen truncation silently
    over-weighting whichever category's occurrence happened to survive the
    dedup earliest.
    """
    # `winners` simulates select_winners()'s output after a dedup collapsed
    # a shared "about" URL down to funding's occurrence (it won on some
    # unrelated quality signal) — `careers` lost that one entirely, but
    # still contributed a *different*, unique winner (`winner_c`) that
    # naturally sorts later in first-occurrence order.
    winner_a = _doc(_FUNDING, 0, url="https://acme.example.com/about")  # won the funding/careers duplicate
    winner_d = _doc(_SIZE, 0)
    winner_b = _doc(_FUNDING, 1)
    winner_c = _doc(_CAREERS, 1)
    winner_e = _doc(_SIZE, 1)
    winners = [winner_a, winner_d, winner_b, winner_c, winner_e]
    category_by_ref = {
        winner_a.ref: _FUNDING, winner_d.ref: _SIZE, winner_b.ref: _FUNDING,
        winner_c.ref: _CAREERS, winner_e.ref: _SIZE,
    }
    naive_truncated = winners[:3]
    naive_counts = Counter(category_by_ref[d.ref] for d in naive_truncated)
    assert naive_counts == {_FUNDING: 2, _SIZE: 1}  # imbalanced: careers got nothing

    ordered = _category_balanced_extract_order(
        winners, category_order=_CATEGORY_ORDER, category_by_ref=category_by_ref
    )
    balanced_truncated = ordered[:3]
    balanced_counts = Counter(category_by_ref[d.ref] for d in balanced_truncated)
    assert balanced_counts == {_FUNDING: 1, _CAREERS: 1, _SIZE: 1}


def test_category_balanced_extract_order_preserves_within_category_quality_order() -> None:
    winners = [_doc(_FUNDING, 0), _doc(_FUNDING, 1), _doc(_CAREERS, 0)]
    category_by_ref = {
        winners[0].ref: _FUNDING, winners[1].ref: _FUNDING, winners[2].ref: _CAREERS,
    }
    ordered = _category_balanced_extract_order(
        winners, category_order=_CATEGORY_ORDER, category_by_ref=category_by_ref
    )
    funding_only = [d for d in ordered if category_by_ref[d.ref] == _FUNDING]
    assert funding_only == [winners[0], winners[1]]  # order within a bucket never reshuffled


# --- integration-level: fetch_sources() end-to-end --------------------------


def _company() -> CompanySeed:
    return CompanySeed(
        slug="acme", name="Acme Robotics", domain="acme.example.com",
        industry="unknown", size_band="unknown", employee_count=0,
    )


def _spec(**overrides) -> PlaySpec:
    defaults = dict(objective_text="find companies", target_industries=["robotics"])
    defaults.update(overrides)
    return PlaySpec(**defaults)


async def test_fetch_sources_query_count_matches_play_spec_shape() -> None:
    """Emitted query counts are exactly 3/4/4/5 for the four approved
    PlaySpec shapes, verified at the real transport-call level."""
    for persona_titles, target_technologies, expected_calls in (
        ([], [], 3),
        (["VP of Sales"], [], 4),
        ([], ["kubernetes"], 4),
        (["VP of Sales"], ["kubernetes"], 5),
    ):
        # Empty results at every category -> no winners -> no extract call
        # is ever issued, so the script needs exactly `expected_calls` steps.
        steps = [(200, search_response(results=[])) for _ in range(expected_calls)]
        provider, transport = make_search_provider(
            steps, max_source_queries_per_prospect=5, max_result_occurrences_per_prospect=15,
        )
        spec = _spec(persona_titles=persona_titles, target_technologies=target_technologies)
        await provider.fetch_sources(_company(), spec, ctx_key="run1:p1:research")
        assert transport.calls == expected_calls


async def test_fetch_sources_balances_occurrences_across_all_three_baseline_categories() -> None:
    """RC-1, end-to-end: funding/careers/size each return 5 hits, ceiling is
    6 (2 per category) — every category must be represented, never just the
    first two in fixed order."""
    per_category_results = [
        search_result(id=f"f{i}", url=f"https://acme.example.com/funding/{i}", content=f"funding {i}")
        for i in range(5)
    ]
    careers_results = [
        search_result(id=f"c{i}", url=f"https://acme.example.com/careers/{i}", content=f"careers {i}")
        for i in range(5)
    ]
    size_results = [
        search_result(id=f"s{i}", url=f"https://acme.example.com/size/{i}", content=f"size {i}")
        for i in range(5)
    ]
    provider, transport = make_search_provider(
        [
            (200, search_response(results=per_category_results)),
            (200, search_response(results=careers_results)),
            (200, search_response(results=size_results)),
            (200, extract_response()),
        ],
        max_source_queries_per_prospect=3, max_result_occurrences_per_prospect=6, max_sources_per_prospect=6,
    )
    spec = _spec(persona_titles=[], target_technologies=[])
    bundle = await provider.fetch_sources(_company(), spec, ctx_key="run1:p1:research")
    urls = {d.url for d in bundle.documents}
    assert any(u.startswith("https://acme.example.com/funding/") for u in urls)
    assert any(u.startswith("https://acme.example.com/careers/") for u in urls)
    assert any(u.startswith("https://acme.example.com/size/") for u in urls)
    assert len(bundle.documents) == 6


async def test_fetch_sources_never_sends_duplicate_urls_to_extract() -> None:
    """The additional implementation invariant: the same URL returned under
    two different query templates (funding and careers both surface the
    company's About page) must be assigned to exactly one winner and must
    never appear twice in the batched `.extract()` request."""
    dup_url = "https://acme.example.com/about"
    provider, transport = make_search_provider(
        [
            (200, search_response(results=[
                search_result(id="f1", url=dup_url, content="Acme raised a Series B round of funding today.")
            ])),
            (200, search_response(results=[search_result(id="c1", url=dup_url, content="short")])),
            (200, search_response(results=[
                search_result(id="s1", url="https://acme.example.com/size", content="Acme has 50 employees.")
            ])),
            (200, extract_response(results=[
                {"url": dup_url, "raw_content": "Full funding text."},
                {"url": "https://acme.example.com/size", "raw_content": "Full size text."},
            ])),
        ],
        max_source_queries_per_prospect=3, max_result_occurrences_per_prospect=15, max_sources_per_prospect=5,
    )
    spec = _spec(persona_titles=[], target_technologies=[])
    bundle = await provider.fetch_sources(_company(), spec, ctx_key="run1:p1:research")

    urls_in_documents = [d.url for d in bundle.documents if d.url == dup_url]
    # The occurrence-level record legitimately keeps both retrieval
    # occurrences (H1's documented "same page returned by 3 queries is 3
    # occurrences" rule) — uniqueness is a *winner*/extract-request property.
    assert len(urls_in_documents) == 2

    extract_request = transport.requests[-1]
    body = json.loads(extract_request.content)
    assert body["urls"].count(dup_url) == 1


async def test_fetch_sources_partial_success_one_category_failing_does_not_starve_others() -> None:
    """A category's search call exhausting its transport retries degrades
    only that category — the other categories still contribute occurrences
    and winners, and the prospect's retrieval overall still succeeds."""
    provider, transport = make_search_provider(
        [
            httpx.ConnectTimeout("boom"),
            httpx.ConnectTimeout("boom again"),  # funding: both attempts fail (1 retry configured)
            (200, search_response(results=[])),  # careers: legitimate empty result
            (200, search_response(results=[search_result(url="https://acme.example.com/size", content="Acme size page.")])),
            (200, extract_response(results=[{"url": "https://acme.example.com/size", "raw_content": "Full size text."}])),
        ],
        settings_overrides={"search_max_transport_retries": 1},
        max_source_queries_per_prospect=3, max_result_occurrences_per_prospect=15, max_sources_per_prospect=5,
    )
    spec = _spec(persona_titles=[], target_technologies=[])
    bundle = await provider.fetch_sources(_company(), spec, ctx_key="run1:p1:research")
    assert len(bundle.documents) == 1
    assert bundle.documents[0].url == "https://acme.example.com/size"
    from groundwork.providers.base import SearchAttemptStatus
    assert any(t.status == SearchAttemptStatus.TIMEOUT for t in bundle.telemetry)
