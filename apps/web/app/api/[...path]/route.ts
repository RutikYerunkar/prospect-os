/**
 * Same-origin API proxy (BFF) — Checkpoint I2.
 *
 * The frontend and the FastAPI backend are deployed as two separate
 * *.onrender.com sites (no custom domain), which means the operator session
 * cookie — host-only, Secure, HttpOnly, SameSite=Lax, no `Domain=` attribute
 * (see `apps/api/groundwork/api/routers/operator.py::_cookie_kwargs`) —
 * cannot represent the intended single-site session topology while the
 * browser talks to the API cross-origin. This route makes the browser talk
 * to exactly one origin (`https://groundwork-web-febu.onrender.com/api/...`)
 * and forwards those requests, server-to-server, to the real API named by
 * the server-only `GROUNDWORK_API_ORIGIN` env var. Nothing about the API's
 * own auth model changes: this proxy relays the same cookie, the same
 * Origin header, and the same status codes/bodies the browser would have
 * gotten talking to the API directly — it does not itself authenticate,
 * authorize, or cache anything.
 *
 * Request bodies are bounded (this API's largest legitimate body is capped
 * at `MAX_REQUEST_BODY_BYTES`, currently 256KB — see `groundwork/config.py`)
 * so they're read fully into memory and re-sent with an accurate
 * Content-Length, rather than streamed — streaming a request body through
 * `fetch` requires Node's `duplex: "half"` and buys nothing at this size.
 * Response bodies are the opposite: the SSE endpoint
 * (`GET /api/runs/{id}/events`) is long-lived and unbounded, so
 * `upstreamResponse.body` (a `ReadableStream`) is wired directly into the
 * returned `Response` — never collected into a string/buffer first. When
 * the browser disconnects, Next.js cancels that stream, which cancels the
 * underlying `fetch` read and closes the upstream connection in turn — the
 * same signal `_event_stream()`'s `request.is_disconnected()` check relies
 * on to stop replaying a run nobody is listening to anymore.
 */

import { NextRequest } from "next/server";
import { buildProxyRequestHeaders, buildProxyResponseHeaders } from "@/lib/proxyHeaders";

// Never statically optimized/cached — every request (including GET) must
// reach the real API fresh; a cached play/run/prospect read would be a
// correctness bug, not a performance win.
export const dynamic = "force-dynamic";
export const revalidate = 0;
export const runtime = "nodejs";

const BODYLESS_METHODS = new Set(["GET", "HEAD"]);

function problem(status: number, title: string, detail: string): Response {
  return Response.json({ type: "about:blank", title, detail, status }, { status });
}

// Local-dev convenience only, matching the old NEXT_PUBLIC_API_URL default
// (see .env.example) — a production deployment always sets
// GROUNDWORK_API_ORIGIN explicitly; this fallback is never a real target.
const LOCAL_DEV_DEFAULT_ORIGIN = "http://localhost:8000";

/** Reads the server-only upstream origin at request time, not at module load
 * — this route has no build-time dependency on `GROUNDWORK_API_ORIGIN` at
 * all, so the API's real URL can change without a frontend rebuild. */
function resolveUpstreamOrigin(): string {
  const origin = process.env.GROUNDWORK_API_ORIGIN || LOCAL_DEV_DEFAULT_ORIGIN;
  return origin.replace(/\/+$/, "");
}

async function proxy(request: NextRequest): Promise<Response> {
  const origin = resolveUpstreamOrigin();

  // `nextUrl.pathname`/`search` reproduce the exact path and query string
  // the browser requested (this route matches everything under `/api/`, so
  // the pathname already IS the upstream path) — no manual reconstruction
  // from the dynamic-segment params needed, and nothing to re-encode.
  const upstreamUrl = `${origin}${request.nextUrl.pathname}${request.nextUrl.search}`;
  const headers = buildProxyRequestHeaders(request.headers);
  const hasBody = !BODYLESS_METHODS.has(request.method);
  const rawBody = hasBody ? await request.arrayBuffer() : undefined;
  // A genuinely bodyless request (this app's own DELETE calls, e.g.
  // operator logout) must pass `undefined`, never a zero-length
  // ArrayBuffer: some undici versions (confirmed on Node 20.9.0's bundled
  // build, this repo's own pinned minimum) mis-derive Content-Length for a
  // present-but-empty body and throw UND_ERR_REQ_CONTENT_LENGTH_MISMATCH,
  // which this route would otherwise surface as an incorrect 502.
  const body = rawBody && rawBody.byteLength > 0 ? rawBody : undefined;

  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body,
      // Faithfully report a 3xx as-is rather than silently following it —
      // this API never redirects today, but a proxy that hides that from
      // the caller isn't "preserving upstream status code".
      redirect: "manual",
      cache: "no-store",
      // Aborts the upstream call the moment the browser disconnects, so a
      // dropped/reconnected SSE connection (the normal case — see
      // `lib/useRunStream.ts`'s manual reconnect loop) doesn't leave an
      // orphaned long-lived request running against the API.
      signal: request.signal,
    });
  } catch {
    return problem(502, "Bad Gateway", "could not reach the Groundwork API");
  }

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: buildProxyResponseHeaders(upstreamResponse.headers),
  });
}

export {
  proxy as GET,
  proxy as POST,
  proxy as PUT,
  proxy as PATCH,
  proxy as DELETE,
  proxy as HEAD,
};
