"""Pure, deterministic contact-identity primitives (v2 §3.7/§3.8).

Everything in this module is offline and side-effect-free: no LLM, no fuzzy
matching, no edit distance, no provider or repository import. Two identifier
classes are handled, mirroring `Evidence._no_fake_sources`'s "origin decides
which shape is legal" discipline:

- email identity normalization (§3.8) — used both by the recipient-level
  send-duplication rule and by the content hash (`domain/content_hash.py`),
  so the two can never disagree;
- LinkedIn identifier grammar + deterministic identity matching (§3.7) — an
  observation's `origin` selects exactly one of two mutually exclusive
  identifier shapes, and a match verdict is computed from plain string
  comparison only.

Versioned per the frozen plan: `EMAIL_IDENTITY_VERSION`,
`IDENTIFIER_GRAMMAR_VERSION`, `IDENTITY_MATCH_VERSION` — all `"v1"`, stored
in `contact_channels.derivation_version` once V2-C wires persistence.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from urllib.parse import urlsplit

import idna

from groundwork.domain.psl import canonical_domain
from groundwork.domain.url_safety import is_safe_source_url
from groundwork.models.enums import (
    EmailDiscoveryState,
    EmailVerificationState,
    EnrichmentOrigin,
    LinkedInIdentityState,
    LinkedInResolutionState,
)
from groundwork.models.schemas import (
    DEMO_LINKEDIN_URL_PATTERN,
    ProviderEmailObservation,
    ProviderLinkedInObservation,
)

EMAIL_IDENTITY_VERSION = "v1"
IDENTIFIER_GRAMMAR_VERSION = "v1"
IDENTITY_MATCH_VERSION = "v1"


class InvalidEmailIdentity(ValueError):
    """Raised by `normalize_email_identity` on any malformed input — always
    fail closed, never a silent pass-through."""


# =====================================================================
# §3.8 — email identity normalization
# =====================================================================


def normalize_email_identity(raw: str) -> str:
    """Canonical `local@domain` key for one email address.

    Two normalization decisions, both made in the fail-closed direction
    (documented here rather than left for the next reader to guess at):

    - **The local part IS casefolded.** RFC 5321 says the local part is
      technically case-sensitive and only the owning server may declare
      otherwise. For a *safety* rule, over-blocking is harmless and
      under-blocking is the actual harm — treating `Priya@x.com` and
      `priya@x.com` as two identities would permit a second initial send to
      what is, in every real deployment, one human. Deliberately
      over-inclusive.
    - **Plus-tags and dots are NOT stripped.** Those are provider-specific
      folding rules (Gmail's, not the internet's). Applying them universally
      would silently merge genuinely distinct mailboxes at providers that
      treat them as significant. The line is: apply only normalizations that
      are universally true; refuse provider-specific folding.

    Idempotent: `normalize_email_identity(normalize_email_identity(x)) ==
    normalize_email_identity(x)` — a property test in
    `tests/test_email_identity_normalization.py`.
    """
    s = unicodedata.normalize("NFKC", raw).strip()
    if s.count("@") != 1:
        raise InvalidEmailIdentity(f"expected exactly one '@' in an email identity, found {s.count('@')}")
    local, domain = s.rsplit("@", 1)
    if not local or not domain:
        raise InvalidEmailIdentity("local part and domain must both be non-empty")
    domain = domain.rstrip(".").casefold()
    if not domain:
        raise InvalidEmailIdentity("domain must not be empty once a trailing '.' is stripped")
    try:
        # uts46=True applies the same Unicode-compatibility mapping browsers
        # use before punycode-encoding, so full-width/mixed-script variants
        # of one domain collapse to the same ASCII (A-label) key alongside
        # already-ASCII and already-punycode input — never a second,
        # divergent normalization path for those.
        domain = idna.encode(domain, uts46=True).decode("ascii")
    except idna.IDNAError as exc:
        raise InvalidEmailIdentity(f"invalid domain in email identity: {exc}") from exc
    local = local.casefold()
    return f"{local}@{domain}"


# =====================================================================
# §3.7 Step 0 — origin-aware LinkedIn identifier grammar
# =====================================================================


class IdentifierVerdict(StrEnum):
    ABSENT = "ABSENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


# `demo://linkedin/<slug>` — the ONLY grammar a DEMO_FIXTURE row may carry.
# Pattern source of truth lives in `models/schemas.py` alongside the
# `ContactEnrichment` model validator that enforces the same grammar at
# persistence time, so the two enforcement points can never drift apart.
_DEMO_LINKEDIN_RE = re.compile(DEMO_LINKEDIN_URL_PATTERN)
# `/in/<id>` — the approved LIVE_PROVIDER path grammar.
_LIVE_LINKEDIN_PATH_RE = re.compile(r"^/in/[A-Za-z0-9\-_%]{1,120}/?$")


def validate_linkedin_identifier(raw: str | None, *, origin: EnrichmentOrigin) -> IdentifierVerdict:
    """Selects exactly one of two mutually exclusive grammars by `origin` — a
    structural fact about which provider produced the row, never an
    inference and never an LLM judgement. Each grammar rejects the other's
    shape: a `demo://` value can never pass the LIVE_PROVIDER grammar (it
    fails the scheme check), and an `http(s)://` value can never pass the
    DEMO_FIXTURE grammar (it fails the exact-match regex).

    `REJECTED` is the fail-closed result for anything malformed — the URL
    never becomes a `RESOLVED` identifier, so it can never be surfaced or
    acted on. This is one of two enforcement points (the other is a
    `contact_enrichments` model validator, §H) — the "secrets are scrubbed
    twice, not once" discipline.
    """
    if raw is None:
        return IdentifierVerdict.ABSENT

    if origin is EnrichmentOrigin.DEMO_FIXTURE:
        return IdentifierVerdict.ACCEPTED if _DEMO_LINKEDIN_RE.match(raw) else IdentifierVerdict.REJECTED

    if origin is EnrichmentOrigin.LIVE_PROVIDER:
        if raw.startswith("demo://"):
            return IdentifierVerdict.REJECTED
        if not is_safe_source_url(raw):
            return IdentifierVerdict.REJECTED
        parts = urlsplit(raw)
        if parts.scheme != "https":
            return IdentifierVerdict.REJECTED
        if parts.username or parts.password:
            return IdentifierVerdict.REJECTED
        if parts.port is not None:
            return IdentifierVerdict.REJECTED
        if parts.fragment:
            return IdentifierVerdict.REJECTED
        if canonical_domain(parts.hostname or "") != "linkedin.com":
            return IdentifierVerdict.REJECTED
        if not _LIVE_LINKEDIN_PATH_RE.match(parts.path):
            return IdentifierVerdict.REJECTED
        return IdentifierVerdict.ACCEPTED

    return IdentifierVerdict.REJECTED


# =====================================================================
# §3.7 Step 1 — text normalization, applied identically to both sides
# =====================================================================


def _norm_text(value: str) -> str:
    """NFKC -> ASCII-fold (drop combining marks) -> casefold -> punctuation/
    symbol characters become spaces -> collapse whitespace. `"José"` ->
    `"jose"`."""
    s = unicodedata.normalize("NFKC", value)
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = unicodedata.normalize("NFC", s)
    s = s.casefold()
    s = "".join(" " if unicodedata.category(ch)[0] in ("P", "S") else ch for ch in s)
    return re.sub(r"\s+", " ", s).strip()


# =====================================================================
# §3.7 Step 2 — person-name matching
# =====================================================================


class PersonMatch(StrEnum):
    PERSON_UNKNOWN = "PERSON_UNKNOWN"
    PERSON_MATCH = "PERSON_MATCH"
    PERSON_CONFLICT = "PERSON_CONFLICT"


_HONORIFIC_TOKENS = frozenset(
    {
        "mr", "mrs", "ms", "mx", "dr", "prof", "rev", "jr", "sr",
        "ii", "iii", "iv", "v", "phd", "md", "mba", "cfa", "cpa", "pmp", "esq",
    }
)


def _person_tokens(name: str | None) -> list[str]:
    if not name:
        return []
    return [t for t in _norm_text(name).split(" ") if t and t not in _HONORIFIC_TOKENS]


def _is_initial_match(a: str, b: str) -> bool:
    """A single-character token is an initial (§3.7 Step 2)."""
    if len(a) == 1:
        return b.startswith(a)
    if len(b) == 1:
        return a.startswith(b)
    return False


def match_person(name_a: str | None, name_b: str | None) -> PersonMatch:
    """Middle tokens ignored. Nicknames are not matched — `jon` vs `john` is
    a `PERSON_CONFLICT`, by design (no fuzzy matching, no edit distance)."""
    tokens_a = _person_tokens(name_a)
    tokens_b = _person_tokens(name_b)
    if len(tokens_a) < 2 or len(tokens_b) < 2:
        return PersonMatch.PERSON_UNKNOWN

    first_a, last_a = tokens_a[0], tokens_a[-1]
    first_b, last_b = tokens_b[0], tokens_b[-1]
    if last_a != last_b:
        return PersonMatch.PERSON_CONFLICT
    if first_a == first_b or _is_initial_match(first_a, first_b):
        return PersonMatch.PERSON_MATCH
    return PersonMatch.PERSON_CONFLICT


# =====================================================================
# §3.7 Step 3 — company matching, strict precedence
# =====================================================================


class CompanyMatch(StrEnum):
    COMPANY_UNKNOWN = "COMPANY_UNKNOWN"
    COMPANY_MATCH = "COMPANY_MATCH"
    COMPANY_CONFLICT = "COMPANY_CONFLICT"


_CORPORATE_SUFFIX_TOKENS = frozenset(
    {
        "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
        "plc", "gmbh", "ag", "sa", "sas", "srl", "spa", "bv", "nv", "ab", "oy",
        "as", "aps", "pty", "pte", "kk", "holdings", "group",
    }
)


def _strip_corporate_suffixes(tokens: list[str]) -> list[str]:
    """Strips only trailing tokens in the approved corporate-suffix set.
    Never removes identity-bearing words like `labs`, `ai`, `technologies`,
    `systems` — those are not in the set, so they always survive."""
    result = list(tokens)
    while result and result[-1] in _CORPORATE_SUFFIX_TOKENS:
        result.pop()
    return result


def match_company(
    *,
    name_a: str | None,
    name_b: str | None,
    domain_a: str | None = None,
    domain_b: str | None = None,
) -> CompanyMatch:
    """Domain equality takes precedence over name equality whenever a domain
    is available on BOTH sides — `ProviderLinkedInObservation.
    asserted_company_domain` is populated only if the provider supplies it,
    so this must (and does) fall through correctly to name matching when
    only one side (or neither) has a domain."""
    if domain_a and domain_b:
        registrable_a = canonical_domain(domain_a)
        registrable_b = canonical_domain(domain_b)
        if registrable_a and registrable_b:
            return CompanyMatch.COMPANY_MATCH if registrable_a == registrable_b else CompanyMatch.COMPANY_CONFLICT

    if not name_a or not name_b:
        return CompanyMatch.COMPANY_UNKNOWN

    tokens_a = _strip_corporate_suffixes(_norm_text(name_a).split(" "))
    tokens_b = _strip_corporate_suffixes(_norm_text(name_b).split(" "))
    if not tokens_a or not tokens_b:
        return CompanyMatch.COMPANY_UNKNOWN
    return CompanyMatch.COMPANY_MATCH if tokens_a == tokens_b else CompanyMatch.COMPANY_CONFLICT


# =====================================================================
# §3.7 Step 4 — combination, fail-closed on contradiction
# =====================================================================


def combine_identity(person: PersonMatch, company: CompanyMatch) -> LinkedInIdentityState:
    """A contradiction on either axis is `MISMATCH` even when the other
    matches — a right name at the wrong company is precisely what must not
    be actionable."""
    if person is PersonMatch.PERSON_CONFLICT or company is CompanyMatch.COMPANY_CONFLICT:
        return LinkedInIdentityState.MISMATCH
    if person is PersonMatch.PERSON_MATCH and company is CompanyMatch.COMPANY_MATCH:
        return LinkedInIdentityState.STRONG_MATCH
    if person is PersonMatch.PERSON_MATCH and company is CompanyMatch.COMPANY_UNKNOWN:
        return LinkedInIdentityState.WEAK_MATCH
    if person is PersonMatch.PERSON_UNKNOWN and company is CompanyMatch.COMPANY_MATCH:
        return LinkedInIdentityState.WEAK_MATCH
    return LinkedInIdentityState.UNKNOWN


# =====================================================================
# Derivations — providers return observations; domain/ derives states (D2)
# =====================================================================


def derive_linkedin_channel(
    obs: ProviderLinkedInObservation | None,
    *,
    origin: EnrichmentOrigin,
    grounded_full_name: str | None,
    grounded_company_name: str | None,
    grounded_company_domain: str | None,
) -> tuple[LinkedInResolutionState, LinkedInIdentityState]:
    """§3.7, Steps 0-4 combined. A rejected identifier never becomes
    `RESOLVED`, so it can never be surfaced or acted on — the identity match
    state is `UNKNOWN` in that case, not evaluated further."""
    if obs is None or obs.profile_url is None:
        return LinkedInResolutionState.NOT_FOUND, LinkedInIdentityState.UNKNOWN

    if validate_linkedin_identifier(obs.profile_url, origin=origin) is not IdentifierVerdict.ACCEPTED:
        return LinkedInResolutionState.NOT_FOUND, LinkedInIdentityState.UNKNOWN

    person = match_person(grounded_full_name, obs.asserted_full_name)
    company = match_company(
        name_a=grounded_company_name,
        name_b=obs.asserted_company_name,
        domain_a=grounded_company_domain,
        domain_b=obs.asserted_company_domain,
    )
    return LinkedInResolutionState.RESOLVED, combine_identity(person, company)


def derive_email_channel(
    obs: ProviderEmailObservation | None,
    *,
    status_map: dict[str, EmailVerificationState],
) -> tuple[EmailDiscoveryState, EmailVerificationState]:
    """From a single SUCCESSFUL enrichment observation (§3.6) — never called
    for a failed call; see `email_discovery_state_after_failed_call` for
    that path. `status_map` maps the provider's own raw status word
    (verbatim, casefolded) to an `EmailVerificationState`; an unmapped
    status fails closed to `UNVERIFIED` — never silently treated as
    verified. Kept adapter-agnostic (a plain string-keyed map, not an
    Apollo-specific type) so this stays pure and provider-neutral (D2):
    `domain/` never contains a provider's name."""
    if obs is None or not obs.address:
        return EmailDiscoveryState.NOT_FOUND, EmailVerificationState.UNVERIFIED
    key = (obs.provider_status or "").strip().casefold()
    verification = status_map.get(key, EmailVerificationState.UNVERIFIED)
    return EmailDiscoveryState.FOUND, verification


def email_discovery_state_after_failed_call(
    existing_discovery_state: EmailDiscoveryState | None,
) -> EmailDiscoveryState:
    """§3.6 last-known-good: a failed enrichment call never destroys a
    previously derived, provider-backed state. `PROVIDER_ERROR` is a channel
    state ONLY when no successful provider-backed observation has ever been
    obtained — distinct from `NOT_FOUND` (a successful call that found
    nothing)."""
    if existing_discovery_state in (None, EmailDiscoveryState.NOT_ATTEMPTED):
        return EmailDiscoveryState.PROVIDER_ERROR
    return existing_discovery_state


def linkedin_resolution_state_after_failed_call(
    existing_resolution_state: LinkedInResolutionState | None,
) -> LinkedInResolutionState:
    """The LinkedIn-channel analogue of `email_discovery_state_after_failed_call`."""
    if existing_resolution_state in (None, LinkedInResolutionState.NOT_ATTEMPTED):
        return LinkedInResolutionState.PROVIDER_ERROR
    return existing_resolution_state
