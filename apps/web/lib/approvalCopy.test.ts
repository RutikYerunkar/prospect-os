import { describe, expect, it } from "vitest";
import { approvalEmptyStateCopy } from "@/lib/approvalCopy";
import type { ProspectStatus } from "@/lib/types";

// v2.0.3 — the Approval panel's non-decidable empty-state copy must
// distinguish "no review verdict yet" from "reviewed but not qualified":
// NOT_QUALIFIED can co-occur with a PASS review verdict, so it needs its
// own explanation rather than the generic "never reached a review verdict"
// message.

describe("approvalEmptyStateCopy", () => {
  it("NOT_QUALIFIED renders the qualification-threshold explanation", () => {
    expect(approvalEmptyStateCopy("NOT_QUALIFIED")).toBe(
      "This prospect did not meet the play's qualification threshold, so no outbound action can be approved.",
    );
  });

  it.each<ProspectStatus>(["PENDING", "RUNNING", "DUPLICATE", "FAILED", "TIMED_OUT"])(
    "%s (genuinely no review verdict) retains the existing copy",
    (status) => {
      expect(approvalEmptyStateCopy(status)).toBe(
        "This prospect never reached a review verdict, so there is nothing for a human to decide yet.",
      );
    },
  );

  it("does not use the qualification copy for statuses that DO have a review verdict (PASS/NEEDS_REVIEW/REJECTED)", () => {
    for (const status of ["PASS", "NEEDS_REVIEW", "REJECTED"] as ProspectStatus[]) {
      expect(approvalEmptyStateCopy(status)).not.toContain("qualification threshold");
    }
  });
});
