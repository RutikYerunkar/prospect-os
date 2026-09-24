/**
 * v2.0.3 — pure copy helper for the prospect-detail Approval panel's
 * non-decidable empty state, extracted so it's directly testable under
 * vitest's node-only environment (no jsdom/testing-library — see
 * `vitest.config.mts`), rather than only provable by rendering
 * `ApprovalBar`.
 *
 * Review verdict (PASS/NEEDS_REVIEW/REJECTED — deterministic review) and
 * qualification status (did the prospect clear the play's ICP threshold)
 * are separate axes. NOT_QUALIFIED can co-occur with a PASS review verdict,
 * so it needs its own copy distinct from "never reached a review verdict."
 * NOT_QUALIFIED stays outside DECIDABLE_STATUSES — this only changes the
 * explanation shown, not whether the prospect is decidable.
 */

import type { ProspectStatus } from "@/lib/types";

export function approvalEmptyStateCopy(status: ProspectStatus): string {
  if (status === "NOT_QUALIFIED") {
    return "This prospect did not meet the play's qualification threshold, so no outbound action can be approved.";
  }
  return "This prospect never reached a review verdict, so there is nothing for a human to decide yet.";
}
