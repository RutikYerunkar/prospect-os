"""Offline, public-suffix-aware registrable-domain normalization (H1 Phase 2).

Real company domains span the full public-suffix complexity a hand-rolled
`"strip www, take everything after the last dot"` normalizer cannot handle
correctly: `acme.co.uk` (a two-label *suffix*, not `co.uk` being the
registrable part), `acme.github.io` (a *private* PSL entry — the registrable
identity is `acme.github.io`, not `github.io`), and a bare suffix on its own
(`github.io`, `co.uk`) which is not a company identity at all.

This module is a thin, pure wrapper around a pinned `tldextract` release,
configured so it can never touch the network:

    tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)

`suffix_list_urls=()` is the documented way to disable tldextract's default
behavior of fetching a fresh public suffix list from the network on first
use in a given cache directory — with no URLs configured, it falls back
unconditionally to the snapshot of the public suffix list frozen into the
installed package at build time (`tldextract/.tld_set_snapshot`). No network
call is attempted at import time or at call time, in this process or any
other — verified offline in `tests/test_psl.py` by monkeypatching `socket`
to raise on any connection attempt. `include_psl_private_domains=True` is
what makes `acme.github.io` resolve as its own registrable domain instead of
collapsing to the ICANN-only `github.io`.

Package/version/config recorded in docs/PROGRESS.md (Phase 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import tldextract

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)

# Constructed once at import time. `cache_dir=None` disables tldextract's
# on-disk cache entirely (nothing to invalidate, nothing to write) — with
# suffix_list_urls=() there is nothing to cache anyway, since the extractor
# never fetches. Reused as a module-level singleton; tldextract's extractor
# objects are safe to share/reuse across calls (no per-call mutable state).
_extractor = tldextract.TLDExtract(
    suffix_list_urls=(),
    include_psl_private_domains=True,
    cache_dir=None,
)

TLDEXTRACT_VERSION = getattr(tldextract, "__version__", "unknown")


@dataclass(frozen=True)
class RegistrableDomain:
    """The PSL-aware decomposition of one hostname."""

    subdomain: str
    domain: str
    suffix: str
    is_private: bool

    @property
    def registrable(self) -> str:
        """`domain + suffix`, e.g. `acme.com`, `acme.co.uk`, `acme.github.io`.

        Empty string if `domain` is empty — i.e. the input was a bare public
        or private suffix on its own, which is not a company identity.
        """
        if not self.domain:
            return ""
        if self.suffix:
            return f"{self.domain}.{self.suffix}"
        # No recognized public suffix at all — an unlisted/reserved TLD
        # (RFC 2606's `.example`/`.test`/`.invalid`/`.localhost`, used by
        # this project's own isolation-test fixtures) or a bare single-label
        # host. tldextract deliberately never guesses suffix boundaries for
        # a TLD it doesn't recognize, so rather than collapsing e.g.
        # `alphacanary.example` and `betacanary.example` to the same bare
        # `"example"` "domain" (silently merging two distinct hosts), fall
        # back to the full normalized hostname as given. This is a fallback
        # for *missing PSL data*, not a hand-written suffix rule — it never
        # runs when a real suffix was matched.
        return f"{self.subdomain}.{self.domain}" if self.subdomain else self.domain


def _strip_to_host(raw: str) -> str:
    value = raw.strip().lower()
    value = _SCHEME_RE.sub("", value)
    # Drop userinfo, path, query, fragment — this module normalizes a
    # *hostname*, not a full URL (see domain/url_safety.py for URL-level
    # safety/canonicalization, which rejects credentialed URLs outright
    # rather than silently stripping them).
    value = value.split("@")[-1]
    value = value.split("/")[0].split("?")[0].split("#")[0]
    # Strip an explicit port.
    if value.count(":") == 1:
        value = value.split(":")[0]
    return value.rstrip(".")


def decompose(raw: str) -> RegistrableDomain | None:
    """PSL-aware decomposition of `raw` (a hostname or a URL/domain-ish
    string). Returns `None` for empty/unparseable input."""
    host = _strip_to_host(raw) if raw else ""
    if not host:
        return None
    result = _extractor(host)
    return RegistrableDomain(
        subdomain=result.subdomain,
        domain=result.domain,
        suffix=result.suffix,
        is_private=result.is_private,
    )


def canonical_domain(raw: str) -> str | None:
    """The normalized registrable domain for `raw`, or `None` if it doesn't
    resolve to one (empty input, or a bare public/private suffix with no
    company identity — e.g. `"co.uk"` or `"github.io"` alone).

    Deterministic and offline: same input always produces the same output,
    with no network access ever attempted.
    """
    decomposed = decompose(raw)
    if decomposed is None:
        return None
    registrable = decomposed.registrable
    return registrable or None
