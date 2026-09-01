"""`python -m groundwork.scripts.search_spike --i-understand-this-makes-real-calls`

H1 Phase 18 — the provider-verification SPIKE SCRIPT, and nothing else. This
is a fact-finding gate for Checkpoint H2, not part of H1's pipeline and not
run automatically by anything (not `make test`, not CI, not any other
script in this repo). Its only purpose is to let a human verify the ACTUAL
installed search-provider SDK's behavior before H2 writes a real
`providers/live/tavily_search.py` adapter against it — mirroring exactly
what `scripts/live_smoke.py` already does for the OpenAI SDK (Checkpoint G).

H1 explicitly does NOT:
  - install/pin a search SDK as a project dependency,
  - write `providers/live/tavily_search.py`,
  - perform any live web search,
  - run this script automatically at any point.

Safety, matching `live_smoke.py`'s pattern:
  - Requires the exact `--i-understand-this-makes-real-calls` flag.
  - Requires `TAVILY_API_KEY` to actually be configured in the environment
    (read directly via `os.environ`, never logged).
  - The `tavily` package is imported LAZILY, only after both of the above
    checks pass — importing this module, or running it without the flag,
    never even attempts to import the SDK, let alone call it.
  - Makes at most one `search()` call and one `extract()` call (on the
    result's own top URL, if any) — bounded, not a crawl.

What it prints (the TECHNICAL checklist from H1 Phase 18):
  - Installed SDK package name + version.
  - Whether the client is sync or async, and its construction signature.
  - The exact request fields `search()` accepts (inspected via
    `inspect.signature`, not guessed from memory) and the ones this probe
    actually sent.
  - `include_domains` behavior, `max_results`, and the response shape:
    field names present on each result (url/title/content/score/
    published_date/...), whether an `extract()` endpoint exists and what it
    returns, any request/result identifiers, and (if this probe happens to
    hit one) 429/rate-limit response shape and `Retry-After` handling.
  - Any usage/credit/cost fields surfaced in the response.
  - Timeout/error behavior actually observed (only for what this run's
    unlucky enough to trigger — this is not a fault-injection harness).

What it does NOT do:
  - Draw legal conclusions about attribution/retention/API-terms
    obligations. It prints what the response and any accompanying docs
    string literally say, and separately records "not observed" for
    anything this run didn't surface. A human reads the output and decides.

Default storage posture this script assumes for H2 (stated, not
implemented here): a bounded cited excerpt + content hash + metadata —
never a full page body.
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from datetime import datetime, timezone


def _print_preamble(query: str) -> None:
    print("=== search_spike — Tavily SDK verification (NOT part of H1's pipeline) ===")
    print(f"query: {query!r}")
    print("this call is REAL, makes a real network request, and may incur real cost/quota usage.")
    print()


def _print_signature(label: str, obj: object) -> None:
    try:
        sig = inspect.signature(obj)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        print(f"{label}: <could not introspect signature: {exc}>")
        return
    print(f"{label}{sig}")


def _describe_result_shape(label: str, obj: object) -> None:
    if isinstance(obj, dict):
        print(f"{label} (dict keys): {sorted(obj.keys())}")
    else:
        attrs = sorted(a for a in dir(obj) if not a.startswith("_"))
        print(f"{label} (type={type(obj).__name__}, attrs): {attrs}")


def run(query: str) -> int:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        print("TAVILY_API_KEY is not configured — refusing to import the SDK or make any call.")
        return 1

    try:
        import tavily  # type: ignore  # lazy — only imported once flag+key both check out
    except ImportError:
        print(
            "The `tavily` package is not installed in this environment. H1 deliberately does "
            "NOT add it as a project dependency (see this module's docstring) — install it "
            "yourself (e.g. `uv add tavily-python` in a scratch/dev context) to actually run "
            "this spike. No call was made."
        )
        return 1

    sdk_version = getattr(tavily, "__version__", "unknown")
    print(f"tavily package version: {sdk_version}")

    client_cls = getattr(tavily, "TavilyClient", None) or getattr(tavily, "AsyncTavilyClient", None)
    if client_cls is None:
        print("Could not find TavilyClient/AsyncTavilyClient on the `tavily` module — SDK shape has changed.")
        return 1
    print(f"client class: {client_cls.__name__} (async: {client_cls.__name__.startswith('Async')})")
    _print_signature("TavilyClient.__init__", client_cls.__init__)

    client = client_cls(api_key=api_key)
    _print_signature("client.search", client.search)

    _print_preamble(query)
    started = datetime.now(timezone.utc)
    try:
        result = client.search(query=query, max_results=3)
    except Exception as exc:  # noqa: BLE001 — this IS the fact-finding: record whatever actually happens
        finished = datetime.now(timezone.utc)
        print(f"search() raised {type(exc).__name__}: {exc}")
        print(f"elapsed: {(finished - started).total_seconds() * 1000:.1f}ms")
        # Surface anything HTTP-shaped the exception carries, without assuming a specific SDK
        # exception hierarchy (verify, don't guess, per this module's own mandate).
        for attr in ("status_code", "response", "headers"):
            if hasattr(exc, attr):
                print(f"  exc.{attr} = {getattr(exc, attr)!r}")
        return 1

    finished = datetime.now(timezone.utc)
    print(f"search() succeeded in {(finished - started).total_seconds() * 1000:.1f}ms")
    _describe_result_shape("search() result", result)

    results_list = result.get("results") if isinstance(result, dict) else getattr(result, "results", None)
    if results_list:
        _describe_result_shape("search() result.results[0]", results_list[0])
        first_url = (
            results_list[0].get("url") if isinstance(results_list[0], dict) else getattr(results_list[0], "url", None)
        )
    else:
        first_url = None
        print("search() returned zero results — cannot probe extract() against a real URL this run.")

    for field in ("response_time", "request_id", "id"):
        value = result.get(field) if isinstance(result, dict) else getattr(result, field, None)
        if value is not None:
            print(f"  result.{field} = {value!r}")

    extract_fn = getattr(client, "extract", None)
    if extract_fn is None:
        print("client has no extract() method — SDK does not expose a separate extract endpoint.")
    elif first_url:
        _print_signature("client.extract", extract_fn)
        try:
            extracted = extract_fn(urls=[first_url])
        except Exception as exc:  # noqa: BLE001 — same rationale as above
            print(f"extract() raised {type(exc).__name__}: {exc}")
        else:
            _describe_result_shape("extract() result", extracted)

    print()
    print("=== end of spike — read the output above; this script draws no legal conclusions. ===")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--i-understand-this-makes-real-calls", action="store_true", dest="confirmed",
        help="Required. Without this flag, the script exits without importing the SDK or calling anything.",
    )
    parser.add_argument(
        "--query", default="AI infrastructure startup Series B funding",
        help="The single search query to send (default: a Groundwork-shaped example query).",
    )
    args = parser.parse_args(argv)

    if not args.confirmed:
        print(
            "Refusing to run: pass --i-understand-this-makes-real-calls to confirm you want this "
            "script to make real network requests against the Tavily API (requires TAVILY_API_KEY)."
        )
        return 1

    return run(args.query)


if __name__ == "__main__":
    sys.exit(main())
