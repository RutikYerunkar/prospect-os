import { describe, expect, it } from "vitest";
import { countByStatus, notQualifiedFromVolume } from "@/lib/runCounts";
import type { ProspectSummary } from "@/lib/types";

// v2.0.3 — pure count helpers, extracted so the NOT_QUALIFIED count/card
// gating ("only show when count > 0") is directly testable under vitest's
// node-only environment (no jsdom/testing-library — see vitest.config.mts),
// without rendering RunSummary/MetricGrid.

function prospect(status: ProspectSummary["status"]): ProspectSummary {
  return {
    id: `p-${status}-${Math.random()}`,
    run_id: "run-1",
    company_name: "Acme",
    company_domain: "acme.com",
    stage: "DONE",
    status,
    top_signal: null,
    contact_verification: null,
    contact_name: null,
    icp_score: null,
    confidence: null,
    had_retry: false,
    approval_state: "PENDING",
    error: null,
  };
}

describe("countByStatus", () => {
  it("counts zero when no prospect has the given status", () => {
    const list = [prospect("PASS"), prospect("NEEDS_REVIEW")];
    expect(countByStatus(list, "NOT_QUALIFIED")).toBe(0);
  });

  it("counts every prospect with the given status", () => {
    const list = [prospect("NOT_QUALIFIED"), prospect("PASS"), prospect("NOT_QUALIFIED")];
    expect(countByStatus(list, "NOT_QUALIFIED")).toBe(2);
  });

  it("returns 0 on an empty list", () => {
    expect(countByStatus([], "NOT_QUALIFIED")).toBe(0);
  });

  it("still counts other statuses correctly (not NOT_QUALIFIED-specific)", () => {
    const list = [prospect("PASS"), prospect("PASS"), prospect("REJECTED")];
    expect(countByStatus(list, "PASS")).toBe(2);
    expect(countByStatus(list, "REJECTED")).toBe(1);
  });
});

describe("notQualifiedFromVolume", () => {
  it("returns 0 when NOT_QUALIFIED is absent from by_status (canonical Demo shape)", () => {
    expect(notQualifiedFromVolume({ PASS: 2, NEEDS_REVIEW: 2, REJECTED: 1, DUPLICATE: 1, FAILED: 1 })).toBe(0);
  });

  it("returns the count when NOT_QUALIFIED is present", () => {
    expect(notQualifiedFromVolume({ PASS: 1, NOT_QUALIFIED: 3 })).toBe(3);
  });

  it("returns 0 on an empty map", () => {
    expect(notQualifiedFromVolume({})).toBe(0);
  });
});

// The "only show when count > 0" gating itself — RunSummary/MetricGrid both
// guard their new card/stat with a plain `count > 0` check, tested here as
// the boundary condition the count helpers feed into.
describe("not-qualified card/stat visibility boundary", () => {
  it("is hidden at exactly zero", () => {
    expect(notQualifiedFromVolume({}) > 0).toBe(false);
  });

  it("is shown for any positive count", () => {
    expect(notQualifiedFromVolume({ NOT_QUALIFIED: 1 }) > 0).toBe(true);
  });
});
