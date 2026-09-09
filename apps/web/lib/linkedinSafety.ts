/**
 * Defense-in-depth mirror of `groundwork/domain/contact_identity.py::
 * validate_linkedin_identifier`'s LIVE_PROVIDER grammar (V2-E §10). The
 * backend already gates what CAN become `RESOLVED`; this is a SECOND,
 * independent check run again here before anything becomes a clickable
 * `<a href>` — the same "secrets are scrubbed twice, not once" discipline
 * as the two backend enforcement points (a pure domain check plus a
 * Pydantic model validator).
 *
 * Only a value that is ALL of the following may ever become an href:
 *   - `channel === "linkedin"`
 *   - `origin === "LIVE_PROVIDER"`
 *   - `discoveryState === "RESOLVED"`
 *   - a syntactically safe, canonical `https://[www.]linkedin.com/in/<id>[/]`
 *     URL (see `isSafeLinkedInProfileUrl` below)
 *
 * `demo://...` identifiers (DEMO_FIXTURE origin) must NEVER become an href,
 * regardless of state — they render as plain text / a synthetic chip.
 *
 * Deliberately does NOT use the built-in `URL` parser: the WHATWG URL
 * algorithm normalizes things a strict mirror must not paper over (it
 * silently strips an explicit default port, and some environments treat a
 * backslash as a path separator) — this hand-rolled parser mirrors Python's
 * `urlsplit` semantics instead, so a case rejected on the backend is
 * rejected here too, byte for byte.
 */

const LINKEDIN_HREF_PATH_RE = /^\/in\/[A-Za-z0-9\-_%]{1,120}\/?$/;
const MAX_URL_LENGTH = 2048;

// scheme "://" authority path [?query] [#fragment] — authority captured
// whole (userinfo/host/port all inside), path/query/fragment split after.
const URL_SHAPE_RE = /^([a-zA-Z][a-zA-Z0-9+.-]*):\/\/([^/?#]*)([^?#]*)(\?[^#]*)?(#.*)?$/;

export function isSafeLinkedInProfileUrl(raw: string | null | undefined): boolean {
  if (!raw || raw.length > MAX_URL_LENGTH) return false;
  // Reject whitespace/control characters and backslashes outright — a
  // conservative, defense-in-depth rejection of known browser-URL-parser
  // quirks (backslash-as-slash normalization) this hand-rolled parser
  // doesn't itself need to reproduce.
  if (/[\s\\]/.test(raw)) return false;

  const match = URL_SHAPE_RE.exec(raw);
  if (!match) return false;
  const [, scheme, authority, path, , fragment] = match;

  if (scheme.toLowerCase() !== "https") return false;
  if (fragment) return false;
  if (authority.includes("@")) return false; // userinfo present
  if (authority.includes(":")) return false; // any port present (incl. explicit default)
  if (authority.includes("[") || authority.includes("]")) return false; // no IPv6 literals expected

  const host = authority.toLowerCase();
  if (!host) return false;
  const isLinkedInHost = host === "linkedin.com" || host.endsWith(".linkedin.com");
  if (!isLinkedInHost) return false;

  return LINKEDIN_HREF_PATH_RE.test(path);
}

export function isSafeLinkedInHref(params: {
  channel: string | null | undefined;
  origin: string | null | undefined;
  discoveryState: string | null | undefined;
  identifier: string | null | undefined;
}): boolean {
  const { channel, origin, discoveryState, identifier } = params;
  if (channel !== "linkedin") return false;
  if (origin !== "LIVE_PROVIDER") return false;
  if (discoveryState !== "RESOLVED") return false;
  if (!identifier || identifier.startsWith("demo://")) return false;
  return isSafeLinkedInProfileUrl(identifier);
}
