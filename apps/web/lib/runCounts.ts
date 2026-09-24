/**
 * v2.0.3 — pure count helpers for the NOT_QUALIFIED status, extracted so
 * they're directly testable under vitest's node-only environment (no
 * jsdom/testing-library configured here — see `vitest.config.mts`), rather
 * than only provable by rendering `RunSummary`/`MetricGrid`.
 */

import type { ProspectSummary } from "@/lib/types";

/** `RunSummary`'s own status counts are computed by filtering the live
 * prospect list — same pattern as its existing pass/needsReview/rejected/
 * duplicate/failed counts, generalized so the new NOT_QUALIFIED count reuses
 * it too rather than adding a fifth near-identical inline filter. */
export function countByStatus(prospects: ProspectSummary[], status: ProspectSummary["status"]): number {
  return prospects.filter((p) => p.status === status).length;
}

/** `MetricGrid`'s counts all read from the evaluation payload's
 * `volume.by_status` map (computed server-side, never re-derived from a raw
 * prospect list) — same lookup-with-default pattern already used inline for
 * `TIMED_OUT`/`FAILED`, pulled out here so it's independently testable. */
export function notQualifiedFromVolume(byStatus: Record<string, number>): number {
  return byStatus["NOT_QUALIFIED"] ?? 0;
}
