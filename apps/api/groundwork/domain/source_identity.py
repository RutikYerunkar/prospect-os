"""Source identity and deterministic dedupe (H1 Phase 3/10).

Two related but distinct concepts:

- **Retrieval occurrence**: one `SourceDocument` returned by one provider
  call. The same page can come back through three different search queries
  — that's three occurrences.
- **Unique usable source**: the deterministic *winner* among occurrences
  that share the same source identity. Evidence is created only from
  winners — a page returned three times must never become three Evidence
  rows, three "sources used," or three contributions to
  `evidence_confidence`.

Source identity is `canonical_url` when the document has a safe URL,
otherwise `source_ref` (the required fallback for Demo Mode, whose fixture
sources have no URLs at all — two fixture sources with different refs are
always distinct, never accidentally merged). Occurrences are also merged
when they carry the same non-empty `content_sha256`, even under different
canonical URLs (e.g. a page mirrored at two URLs with identical extracted
text).

Pure, offline, no I/O — safe for `domain/`.
"""

from __future__ import annotations

import hashlib
import uuid

from groundwork.domain.url_safety import canonicalize_url
from groundwork.models.schemas import SourceDocument

# Fixed namespace for deterministic, idempotent Evidence ids (H1 Phase 10):
# `uuid.uuid5(EVIDENCE_ID_NAMESPACE, f"{prospect_id}:{source_identity(doc)}")`
# — the same prospect re-processing the same winning source (e.g. a step
# retry that reuses cached `ctx.sources`) always derives the same Evidence
# id, so a re-commit can never produce a duplicate row for the same source.
EVIDENCE_ID_NAMESPACE = uuid.UUID("6f1e6c0a-6b1a-4f0b-9b1a-2f7e6c0a6b1a")


def compute_content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evidence_id_for(prospect_id: str, doc: SourceDocument) -> str:
    """Deterministic Evidence id for the winning occurrence of one source,
    scoped to one prospect (never shared across prospects, preserving
    isolation)."""
    return str(uuid.uuid5(EVIDENCE_ID_NAMESPACE, f"{prospect_id}:{source_identity(doc)}"))


def source_identity(doc: SourceDocument) -> str:
    """The identity key two occurrences of "the same source" must share."""
    canonical = doc.canonical_url or canonicalize_url(doc.url) if doc.url else doc.canonical_url
    if canonical:
        return f"url:{canonical}"
    return f"ref:{doc.ref}"


class _UnionFind:
    def __init__(self, keys: set[str]) -> None:
        self._parent = {k: k for k in keys}

    def find(self, key: str) -> str:
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def group_occurrences(docs: list[SourceDocument]) -> list[list[SourceDocument]]:
    """Group retrieval occurrences that identify the same source — by
    identity key, then merged further by shared non-empty `content_sha256`.
    Group *membership* is order-independent (same result under any input
    shuffle); the order of the returned groups follows first-occurrence
    order of each group's earliest-seen member.
    """
    if not docs:
        return []

    keys = [source_identity(d) for d in docs]
    uf = _UnionFind(set(keys))

    sha_to_key: dict[str, str] = {}
    for doc, key in zip(docs, keys, strict=True):
        if doc.content_sha256:
            existing = sha_to_key.get(doc.content_sha256)
            if existing is None:
                sha_to_key[doc.content_sha256] = key
            else:
                uf.union(existing, key)

    groups: dict[str, list[SourceDocument]] = {}
    order: list[str] = []
    for doc, key in zip(docs, keys, strict=True):
        root = uf.find(key)
        if root not in groups:
            groups[root] = []
            order.append(root)
        groups[root].append(doc)
    return [groups[root] for root in order]


def _winner_sort_key(doc: SourceDocument):
    """Deterministic total order — see `select_winners()`. Sorting
    ascending (via `min()`) picks the best occurrence first, in this
    priority: (1) successful extraction, (2) longer usable text, (3) known
    `published_at`, (4) higher `relevance_score`, (5) better (lower) `rank`,
    (6) a stable lexicographic tie-break over provider_result_id/ref.
    """
    return (
        0 if doc.status == "ok" else 1,
        -len(doc.text or ""),
        0 if doc.published_at is not None else 1,
        -(doc.relevance_score if doc.relevance_score is not None else -1.0),
        doc.rank if doc.rank is not None else 10**9,
        doc.provider_result_id or doc.ref or "",
    )


def pick_winner(group: list[SourceDocument]) -> SourceDocument:
    """The deterministic winner of one group of equivalent occurrences —
    same result regardless of the group's input order. Public so callers
    that need the per-occurrence winner/loser relationship (H1 Phase 10's
    `canonical_source_id` persistence) can compute groups once via
    `group_occurrences()` and pick each group's winner without duplicating
    the ordering rule."""
    return min(group, key=_winner_sort_key)


def select_winners(docs: list[SourceDocument]) -> list[SourceDocument]:
    """One deterministic winner per distinct source identity. Order of the
    returned list follows `group_occurrences()`'s group order."""
    return [pick_winner(group) for group in group_occurrences(docs)]
