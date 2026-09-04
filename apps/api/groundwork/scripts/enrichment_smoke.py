"""`python -m groundwork.scripts.enrichment_smoke --full-name "..." \\
    --company-domain "..." --i-understand-this-costs-money`

The OPTIONAL real-live V2-D smoke test. Makes real, billed calls to the
Apollo `POST /api/v1/people/match` endpoint against up to 2 REAL people the
operator supplies — never the demo fixture pack, never invented data. This
costs real money and makes a real network call — it must NEVER run
accidentally:

- Requires the exact `--i-understand-this-costs-money` flag.
- Requires `APOLLO_API_KEY` to actually be configured.
- Capped at <= 2 people (`--person` may be given at most twice).
- Never runs as part of `make test`, CI, or any other automated path.
- Never prints the API key (it never even touches this module's own code —
  `ApolloRuntime.create()` puts it straight into the shared `httpx.
  AsyncClient`'s headers).

Unlike `scripts/search_smoke.py`, this does NOT run the full engine — it
calls `ApolloEnrichmentProvider._issue()` directly, ONE raw HTTP call per
person, deliberately bypassing `enrich_person()`'s strict-envelope
raise-on-anything-but-a-match behavior so this script can inspect and print
whatever Apollo actually returns (a real match, OR whatever shape a genuine
no-match turns out to have — still unverified as of V2-D). That is this
smoke's entire purpose: CONFIRM real authentication, real matching
behavior, the exact HTTP-200 no-match representation (if one is
encountered), request-id availability, and any numeric usage/credit field
— never to discover new product behavior.

This script does not assert PASS/FAIL on Apollo's own business outcome (a
real no-match is a legitimate result, not a smoke failure). It FAILS loudly
(nonzero exit) only on a structural problem: `APOLLO_API_KEY` missing, the
confirmation flag missing, or an uncaught exception while issuing the call.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from groundwork.config import settings
from groundwork.providers.contact_base import EnrichmentAttemptStatus, PersonEnrichmentQuery
from groundwork.providers.live.apollo_enrichment import ApolloEnrichmentProvider
from groundwork.providers.live.enrichment_runtime import APOLLO_API_ORIGIN, APOLLO_PEOPLE_MATCH_PATH, ApolloRuntime


@dataclass
class _Person:
    full_name: str
    company_domain: str
    title: str | None = None


def _print_preamble(people: list[_Person]) -> None:
    print("=== Groundwork V2-D Apollo enrichment smoke test — REAL Apollo call, REAL cost ===")
    print(f"endpoint:                {APOLLO_API_ORIGIN}{APOLLO_PEOPLE_MATCH_PATH}")
    print(f"people to look up:       {len(people)} (capped at 2)")
    for i, p in enumerate(people, start=1):
        print(f"  [{i}] full_name={p.full_name!r} company_domain={p.company_domain!r} title={p.title!r}")
    print(f"call deadline (s):       {settings.apollo_call_deadline_s}")
    print(f"max transport retries:   {settings.apollo_max_transport_retries} (this script makes exactly 1 attempt/person — no retry loop)")
    pricing_ok = settings.apollo_price_usd_per_credit is not None
    print(f"pricing configured:      {pricing_ok} (moot — no verified numeric usage field exists yet)")
    print()


def _summarize_raw(raw: dict | None) -> None:
    if raw is None:
        print("  raw body: (none — non-JSON or no body)")
        return
    print(f"  raw top-level keys: {sorted(raw.keys())}")
    person = raw.get("person")
    if isinstance(person, dict):
        print(f"  person keys: {sorted(person.keys())}")
        print(f"  person.id present: {bool(person.get('id'))}")
    elif "person" in raw:
        print(f"  person value (not a dict): {person!r}")
    # Look for anything usage/credit-shaped without assuming a name — purely
    # informational, never fed into `credits_used` by the adapter itself.
    usage_like = {k: v for k, v in raw.items() if "credit" in k.lower() or "usage" in k.lower()}
    if usage_like:
        print(f"  possible usage/credit fields observed: {usage_like}")
    else:
        print("  no usage/credit-shaped top-level field observed")


async def _run_one(provider: ApolloEnrichmentProvider, person: _Person) -> bool:
    """Returns True iff no structural problem occurred (a real no-match is
    NOT a structural problem)."""
    query = PersonEnrichmentQuery(full_name=person.full_name, title=person.title, company_name="", company_domain=person.company_domain)
    params = {
        "name": query.full_name or "",
        "domain": query.company_domain,
        "reveal_personal_emails": "false",
        "reveal_phone_number": "false",
        "run_waterfall_email": "false",
        "run_waterfall_phone": "false",
    }
    try:
        status, raw, http_status, request_id, error_text = await provider._issue(params)  # noqa: SLF001 — deliberate, see module docstring
    except Exception as exc:  # noqa: BLE001 — a fatal wiring error must FAIL this smoke loudly
        print(f"  FATAL: uncaught exception issuing the call: {exc!r}", file=sys.stderr)
        return False

    print(f"  http_status:    {http_status}")
    print(f"  status:         {status.value}")
    print(f"  provider_request_id: {request_id!r}")
    print(f"  error_text (if any, bounded, never the key): {error_text!r}")
    _summarize_raw(raw)

    if status == EnrichmentAttemptStatus.OK:
        print("  => recognized as a MATCH by the current strict parser.")
    elif status == EnrichmentAttemptStatus.INVALID_RESPONSE and http_status == 200:
        print(
            "  => a 200 response the current strict parser does NOT recognize as a match. "
            "This may be the real no-match shape — inspect the raw body above and, once "
            "confirmed, add a single recognizing branch to apollo_enrichment.py::_issue()."
        )
    return True


async def main(people: list[_Person]) -> int:
    if not settings.apollo_api_key:
        print("APOLLO_API_KEY is not configured — aborting.", file=sys.stderr)
        return 1

    _print_preamble(people)
    runtime = ApolloRuntime.create(settings)
    provider = ApolloEnrichmentProvider(runtime=runtime)

    ok = True
    try:
        for i, person in enumerate(people, start=1):
            print(f"--- person {i}/{len(people)} ---")
            ok = await _run_one(provider, person) and ok
            print()
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
        help="required — this makes a real, billed Apollo API call",
    )
    parser.add_argument(
        "--person", action="append", default=[], metavar="FULL_NAME:COMPANY_DOMAIN[:TITLE]",
        help="a real person to look up, e.g. --person \"Jane Doe:example.com:VP of Sales\". "
        "May be given at most twice.",
    )
    return parser.parse_args()


def _parse_people(raw: list[str]) -> list[_Person]:
    if not raw:
        print("At least one --person is required.", file=sys.stderr)
        sys.exit(1)
    if len(raw) > 2:
        print("At most 2 --person entries are allowed.", file=sys.stderr)
        sys.exit(1)
    people = []
    for entry in raw:
        parts = entry.split(":", 2)
        if len(parts) < 2:
            print(f"Malformed --person {entry!r} — expected FULL_NAME:COMPANY_DOMAIN[:TITLE].", file=sys.stderr)
            sys.exit(1)
        full_name, company_domain = parts[0], parts[1]
        title = parts[2] if len(parts) == 3 else None
        people.append(_Person(full_name=full_name, company_domain=company_domain, title=title))
    return people


if __name__ == "__main__":
    args = parse_args()
    if not args.confirmed:
        print("Refusing to run without --i-understand-this-costs-money.", file=sys.stderr)
        sys.exit(1)
    people = _parse_people(args.person)
    sys.exit(asyncio.run(main(people)))
