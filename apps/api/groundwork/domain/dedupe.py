"""Deduplication — pure normalization and comparison, no I/O.

A model here would be strictly worse and non-reproducible (§8/§9): dedupe is
identity, not judgment.
"""

from __future__ import annotations

import re

_LEGAL_SUFFIXES = (
    " inc.",
    " inc",
    " incorporated",
    " corp.",
    " corp",
    " corporation",
    " llc",
    " l.l.c.",
    " ltd.",
    " ltd",
    " limited",
    " co.",
    " company",
    " plc",
    " gmbh",
)


def normalize_domain(raw: str) -> str:
    """`https://www.Acme.com/` -> `acme.com`."""
    value = raw.strip().lower()
    value = re.sub(r"^[a-z]+://", "", value)
    value = value.split("/")[0]
    if value.startswith("www."):
        value = value[len("www.") :]
    return value


def normalize_name(raw: str) -> str:
    """`Northwind Labs Inc.` -> `northwind labs` (legal suffix stripped)."""
    value = " ".join(raw.strip().lower().split())
    for suffix in _LEGAL_SUFFIXES:
        if value.endswith(suffix):
            value = value[: -len(suffix)].strip()
            break
    value = re.sub(r"[^\w\s]", "", value)
    return " ".join(value.split())


def dedupe_key(domain: str, name: str) -> str:
    """Domain is the precedence signal; normalized name is the fallback.

    Two companies with the same normalized domain are the same account
    regardless of name drift. If no usable domain exists, fall back to the
    normalized name.
    """
    normalized_domain = normalize_domain(domain) if domain else ""
    if normalized_domain:
        return f"domain:{normalized_domain}"
    return f"name:{normalize_name(name)}"


def find_duplicate(key: str, seen_keys: dict[str, str]) -> str | None:
    """Return the id of the earlier prospect sharing `key`, if any."""
    return seen_keys.get(key)
