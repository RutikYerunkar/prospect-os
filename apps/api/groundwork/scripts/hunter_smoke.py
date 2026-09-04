"""`python -m groundwork.scripts.hunter_smoke --i-understand-this-costs-money \\
    --person "Jane Doe:example.com:VP of Sales"`

The OPTIONAL real-live V2-DH smoke test for the Hunter Email Finder adapter.
Makes ONE real, billed call to `GET /v2/email-finder` for a single real
person the operator supplies — never the demo fixture pack, never invented
data. This costs real money and makes a real network call — it must NEVER
run accidentally:

- Requires the exact `--i-understand-this-costs-money` flag.
- Requires `HUNTER_API_KEY` to actually be configured.
- Capped at exactly ONE `--person` (deliberately stricter than Apollo's
  smoke, which allows two).
- Never runs as part of `make test`, CI, or any other automated path.
- Never prints the API key — it never even touches this module's own code;
  `HunterRuntime.create()` puts it straight into the shared `httpx.
  AsyncClient`'s headers.
- Never prints the raw returned email address — only a masked form.

Like `scripts/enrichment_smoke.py`, this does NOT run the full engine — it
issues ONE raw HTTP call directly against the shared `HunterRuntime`
(mirroring `enrich_person()`'s exact request shape, but bypassing its
response-shaping) so this script can inspect the full `httpx.Response`
(status, headers, body) Hunter actually returns. That is this smoke's entire
purpose: close the two remaining wire unknowns — the exact HTTP-200 no-email
response body shape, and whether Hunter's response carries a request-id/
correlation header (and its exact name) — never to discover new product
behavior.

This script does not assert PASS/FAIL on Hunter's own business outcome (a
real no-match is a legitimate result, not a smoke failure). It FAILS loudly
(nonzero exit) only on a structural problem: `HUNTER_API_KEY` missing, the
confirmation flag missing, or an uncaught exception while issuing the call.

Only SAFE, STRUCTURAL observations are ever printed — never raw PII: HTTP
status, verification status/date, score value+type, accept_all value+type,
LinkedIn presence (not the URL), company/position presence (not the
values), source count + distinct hostnames only, the `meta` object's own key
names, any credit/usage-shaped field names+values, request-id/rate-limit/
credit-related response HEADER NAMES (not every header value), and a
best-effort, already-scrubbed `errors[0].id`. The returned email address, if
any, is printed MASKED, never verbatim.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from groundwork.config import settings
from groundwork.providers.contact_base import PersonEnrichmentQuery
from groundwork.providers.live.hunter_runtime import HUNTER_API_ORIGIN, HUNTER_EMAIL_FINDER_PATH, HunterRuntime

# Header-NAME hints only — never a value dump. Matches loosely (substring)
# since the real header naming convention is one of this smoke's own open
# questions.
_INTERESTING_HEADER_HINTS = ("request-id", "ratelimit", "rate-limit", "credit")


@dataclass
class _Person:
    full_name: str
    company_domain: str
    title: str | None = None


def _mask_email(address: str) -> str:
    if "@" not in address:
        return "***"
    local, domain = address.split("@", 1)
    masked_local = f"{local[0]}***" if local else "***"
    return f"{masked_local}@{domain}"


def _print_preamble(person: _Person) -> None:
    print("=== Groundwork V2-DH Hunter enrichment smoke test — REAL Hunter call, REAL cost ===")
    print(f"endpoint:                {HUNTER_API_ORIGIN}{HUNTER_EMAIL_FINDER_PATH}")
    print(
        f"person to look up:      full_name={person.full_name!r} "
        f"company_domain={person.company_domain!r} title={person.title!r}"
    )
    print(f"call deadline (s):       {settings.hunter_call_deadline_s}")
    print(
        f"max transport retries:   {settings.hunter_max_transport_retries} "
        "(this script makes exactly 1 attempt — no retry loop)"
    )
    print("pricing configured:      False (no HUNTER_PRICE_USD_PER_CREDIT setting exists)")
    print()


def _summarize_headers(headers: Any) -> None:
    names = sorted({k for k in headers.keys() if any(hint in k.lower() for hint in _INTERESTING_HEADER_HINTS)})
    print(f"  request-id/rate-limit/credit-related response header names: {names or '(none observed)'}")


def _source_hostnames(sources: Any) -> list[str | None]:
    if not isinstance(sources, list):
        return []
    hostnames = set()
    for entry in sources:
        if not isinstance(entry, dict):
            continue
        uri = entry.get("uri") or entry.get("url")
        if isinstance(uri, str) and uri:
            hostnames.add(urlsplit(uri).hostname)
    return sorted(h for h in hostnames if h)


def _summarize_raw(raw: dict[str, Any] | None) -> None:
    if raw is None:
        print("  raw body: (none — non-JSON, non-dict, or no body)")
        return
    print(f"  raw top-level keys: {sorted(raw.keys())}")

    meta = raw.get("meta")
    if isinstance(meta, dict):
        print(f"  meta keys: {sorted(meta.keys())}")

    data = raw.get("data")
    usage_like = {k: v for k, v in raw.items() if "credit" in k.lower() or "usage" in k.lower()}
    if not isinstance(data, dict):
        print(f"  data field: {data!r} (not an object)")
        if usage_like:
            print(f"  possible usage/credit fields observed at top level: {usage_like}")
        return

    email = data.get("email")
    if isinstance(email, str) and email:
        print(f"  data.email (masked): {_mask_email(email)!r}")
    else:
        print(f"  data.email: {email!r} (no match, or malformed)")

    verification = data.get("verification")
    if isinstance(verification, dict):
        print(f"  data.verification.status: {verification.get('status')!r}")
        print(f"  data.verification.date: {verification.get('date')!r}")
    else:
        print(f"  data.verification: {verification!r}")

    score = data.get("score")
    print(f"  data.score: {score!r} (type={type(score).__name__})")

    accept_all = data.get("accept_all")
    print(f"  data.accept_all: {accept_all!r} (type={type(accept_all).__name__})")

    print(f"  data.linkedin_url present: {bool(data.get('linkedin_url'))}")
    print(f"  data.company present: {bool(data.get('company'))}")
    print(f"  data.position present: {bool(data.get('position'))}")

    sources = data.get("sources")
    hostnames = _source_hostnames(sources)
    print(f"  data.sources count: {len(sources) if isinstance(sources, list) else 0}; distinct hostnames: {hostnames}")

    if usage_like:
        print(f"  possible usage/credit fields observed at top level: {usage_like}")
    else:
        print("  no usage/credit-shaped top-level field observed")


async def _run_one(runtime: HunterRuntime, person: _Person) -> bool:
    """Issues the ONE real HTTP call directly against the shared runtime
    (rather than through `HunterEnrichmentProvider._issue()`, which doesn't
    hand back the full `httpx.Response` this script needs for header-name
    inspection) — same params/method/deadline the real adapter uses.
    Returns True iff no structural problem occurred (a real no-match is NOT
    a structural problem)."""
    query = PersonEnrichmentQuery(
        full_name=person.full_name, title=person.title, company_name="", company_domain=person.company_domain
    )
    params = {"domain": query.company_domain, "full_name": query.full_name or ""}
    try:
        async with runtime.semaphore:
            response = await asyncio.wait_for(
                runtime.client.get(HUNTER_EMAIL_FINDER_PATH, params=params),
                timeout=runtime.call_deadline_s,
            )
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        print(f"  TIMEOUT issuing the call: {exc!r}", file=sys.stderr)
        return True  # a timeout is a legitimate transient outcome, not a structural problem
    except Exception as exc:  # noqa: BLE001 — a fatal wiring error must FAIL this smoke loudly
        print(f"  FATAL: uncaught exception issuing the call: {exc!r}", file=sys.stderr)
        return False

    print(f"  http_status:    {response.status_code}")
    _summarize_headers(response.headers)

    raw: dict[str, Any] | None = None
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            raw = parsed
    except ValueError:
        pass

    error_id = None
    if raw is not None:
        errors = raw.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            candidate = errors[0].get("id")
            error_id = candidate if isinstance(candidate, str) else None
    print(f"  errors[0].id (best-effort, scrubbed): {error_id!r}")

    _summarize_raw(raw)

    if response.status_code == 200 and raw is not None:
        data = raw.get("data")
        matched = isinstance(data, dict) and bool(data.get("email"))
        print(f"  => recognized as a well-formed 200 response; email found: {matched}.")
    elif response.status_code == 200:
        print("  => a 200 response this script could not parse as a JSON object. Inspect the raw body above.")
    return True


async def main(person: _Person) -> int:
    if not settings.hunter_api_key:
        print("HUNTER_API_KEY is not configured — aborting.", file=sys.stderr)
        return 1

    _print_preamble(person)
    runtime = HunterRuntime.create(settings)

    ok = True
    try:
        ok = await _run_one(runtime, person)
    finally:
        await runtime.close()

    if not ok:
        print("FAILURE — a structural problem occurred (see above).", file=sys.stderr)
        return 1
    print("OK — no structural invariant violated. (A real no-match, if any, is a legitimate result, not a failure.)")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--i-understand-this-costs-money", action="store_true", dest="confirmed",
        help="required — this makes a real, billed Hunter API call",
    )
    parser.add_argument(
        "--person", default=None, metavar="FULL_NAME:COMPANY_DOMAIN[:TITLE]",
        help="the ONE real person to look up, e.g. --person \"Jane Doe:example.com:VP of Sales\".",
    )
    return parser.parse_args()


def _parse_person(raw: str | None) -> _Person:
    if not raw:
        print("--person is required.", file=sys.stderr)
        sys.exit(1)
    parts = raw.split(":", 2)
    if len(parts) < 2:
        print(f"Malformed --person {raw!r} — expected FULL_NAME:COMPANY_DOMAIN[:TITLE].", file=sys.stderr)
        sys.exit(1)
    full_name, company_domain = parts[0], parts[1]
    title = parts[2] if len(parts) == 3 else None
    return _Person(full_name=full_name, company_domain=company_domain, title=title)


if __name__ == "__main__":
    args = parse_args()
    if not args.confirmed:
        print("Refusing to run without --i-understand-this-costs-money.", file=sys.stderr)
        sys.exit(1)
    person = _parse_person(args.person)
    sys.exit(asyncio.run(main(person)))
