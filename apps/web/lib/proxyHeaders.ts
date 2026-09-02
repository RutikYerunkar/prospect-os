/**
 * Header-forwarding rules for the same-origin API proxy (`app/api/[...path]/route.ts`,
 * Checkpoint I2). Kept as pure functions, separate from the route handler, so the
 * forwarding/stripping behavior can be unit-tested directly against constructed
 * `Headers` objects without spinning up a fake HTTP server.
 */

/** Hop-by-hop headers (RFC 9110 §7.6.1) — connection-management headers that
 * describe THIS hop only and must never be relayed to/from the next one. */
export const HOP_BY_HOP_HEADERS: ReadonlySet<string> = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "trailers",
]);

/** Response-only strip list. `content-encoding`/`content-length` describe the
 * upstream response's original bytes — undici's `fetch` transparently decodes
 * a compressed body before we ever see it, and we re-wrap that already-decoded
 * body as a fresh stream, so relaying either header would mislabel what's
 * actually being sent (a browser trying to gunzip an already-plain body, or a
 * length that no longer matches a body we didn't re-buffer). */
const RESPONSE_ONLY_STRIP: ReadonlySet<string> = new Set(["content-encoding", "content-length"]);

/** Request headers the browser sends that the proxy explicitly relays to the
 * upstream API, allow-listed rather than denylisted — the smallest set this
 * app's own client (`lib/api.ts`) and CSRF model (`api/live_gate.py::
 * require_allowed_origin`) actually need. `host` is deliberately never in
 * this list (the outbound `fetch` must address the real API origin, not
 * carry the browser's Next.js host along with it) and every hop-by-hop
 * header is excluded by construction, not by a denylist check. */
const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "cookie", "origin"] as const;

export function buildProxyRequestHeaders(source: Headers): Headers {
  const out = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = source.get(name);
    if (value !== null) out.set(name, value);
  }
  return out;
}

export function buildProxyResponseHeaders(source: Headers): Headers {
  const out = new Headers();
  for (const [key, value] of source.entries()) {
    const lower = key.toLowerCase();
    // Set-Cookie is handled separately below via getSetCookie() — a plain
    // `entries()` iteration join multiple Set-Cookie values into one
    // comma-joined string on some Headers implementations, which is not a
    // valid way to send multiple cookies back to the browser.
    if (lower === "set-cookie") continue;
    if (HOP_BY_HOP_HEADERS.has(lower) || RESPONSE_ONLY_STRIP.has(lower)) continue;
    out.set(key, value);
  }
  for (const cookie of source.getSetCookie()) {
    out.append("set-cookie", cookie);
  }
  return out;
}
