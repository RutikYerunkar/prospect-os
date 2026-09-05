import type { GmailBanner } from "@/components/GmailSettingsPanel";

/**
 * Pure, deterministic derivation of the Gmail OAuth result banner from the
 * `?gmail=...&reason=...` query parameters `GET /api/gmail/callback`
 * redirects back with. Used on the SERVER (via `app/settings/page.tsx`'s
 * `searchParams` prop) to compute the value the initial HTML must render,
 * and reused as-is for any later re-derivation — there is exactly one
 * function that decides this, so the server and the client can never
 * disagree about what a given query string means.
 *
 * Reads no browser global (no `window`, no `Date.now()`, no `Math.random()`)
 * — it only inspects the plain object handed to it, which is what makes it
 * safe to call during server rendering.
 *
 * `reason` is validated against the same finite allow-list the backend
 * itself sanitizes to (`api/routers/gmail.py::_ALLOWED_GOOGLE_ERROR_REASONS`)
 * — an unrecognized/arbitrary value collapses to `"unknown"` rather than
 * ever being reflected verbatim into rendered markup.
 */
const ALLOWED_GMAIL_ERROR_REASONS = new Set(["access_denied"]);

function firstValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export function deriveGmailBanner(params: {
  gmail?: string | string[];
  reason?: string | string[];
}): GmailBanner {
  const gmail = firstValue(params.gmail);
  if (gmail === "connected") return { kind: "connected" };
  if (gmail !== "error") return null;

  const rawReason = firstValue(params.reason);
  const reason = rawReason && ALLOWED_GMAIL_ERROR_REASONS.has(rawReason) ? rawReason : "unknown";
  return { kind: "error", reason };
}
