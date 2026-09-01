"""Discovery identity-gate primitives (H1 Phase 14).

H1 does not call a real search provider — these are pure, offline
primitives an H2 real domain-resolution step must route every candidate
through before ever trusting it as a company's own site. Nothing here is
exercised against a live provider in H1; every test in
`tests/test_discovery.py` is offline.

Three gates, all enforced together by `resolve_candidate_domain()`:

- **Served candidate refs only** — a canonical domain can never originate
  from the model's own output; it must have been present in the set of
  domains the provider actually served this call.
- **Structural aggregator filtering** — a small denylist of hosts that
  legitimately show up in search results *about* a company but are never
  that company's own site (LinkedIn, Crunchbase, Wikipedia, social
  platforms, etc.).
- **URL safety** — `domain/url_safety.py`'s structural gate.
"""

from __future__ import annotations

from groundwork.domain.psl import canonical_domain
from groundwork.domain.url_safety import is_safe_source_url

# Aggregator/directory/social hosts that are never a company's own official
# domain, even though they legitimately appear in search results about that
# company. Deliberately small and reviewable — not a hand-rolled substitute
# for a PSL, just a denylist of well-known third parties.
STRUCTURAL_AGGREGATOR_DOMAINS: frozenset[str] = frozenset(
    {
        "linkedin.com",
        "crunchbase.com",
        "wikipedia.org",
        "bloomberg.com",
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "glassdoor.com",
        "indeed.com",
        "github.com",
        "medium.com",
        "reddit.com",
        "pitchbook.com",
        "owler.com",
        "zoominfo.com",
        "producthunt.com",
        "g2.com",
    }
)


def is_structural_aggregator(domain: str) -> bool:
    normalized = canonical_domain(domain) or domain.strip().lower()
    return normalized in STRUCTURAL_AGGREGATOR_DOMAINS


def resolve_candidate_domain(url: str | None, served_domains: frozenset[str]) -> str | None:
    """A candidate domain is trusted as a company's canonical domain ONLY
    if: (a) its URL passes `is_safe_source_url`, (b) it normalizes to a
    non-aggregator registrable domain via `domain/psl.py`, and (c) that
    normalized domain was actually present in `served_domains` — the set of
    domains a provider call served this round, never a domain the model
    merely typed into its own output. Returns `None` if any gate fails.
    """
    if not url or not is_safe_source_url(url):
        return None
    normalized = canonical_domain(url)
    if not normalized or is_structural_aggregator(normalized):
        return None
    if normalized not in served_domains:
        return None
    return normalized


def label_supported_by_sources(label: str, served_refs: frozenset[str], claimed_ref: str | None) -> bool:
    """A company display label is trustworthy only if it cites one of the
    refs actually served to the model for this call — never accepted on
    the model's bare assertion alone."""
    return bool(label.strip()) and claimed_ref is not None and claimed_ref in served_refs
