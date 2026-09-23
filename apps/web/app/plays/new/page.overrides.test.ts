import { describe, expect, it } from "vitest";
import { buildIcpOverrides } from "@/app/plays/new/page";

// v2.0.1 Live-Quality Hardening — regression test for the highest-leverage
// Live UI bug: `icp_overrides` used to be a single unconditional object sent
// for BOTH Demo and Live, so every Live run silently inherited the
// canonical Demo fixture's own funding-stage/technology/persona/exclusion
// targeting. `buildIcpOverrides()` must now be mode-aware.

const FORM = { industries: ["fintech"], sizeMin: 10, sizeMax: 500, minScore: 40 };

describe("buildIcpOverrides", () => {
  it("Demo Mode sends the full canonical fixture ICP, byte-for-byte", () => {
    expect(buildIcpOverrides("demo", FORM)).toEqual({
      target_industries: ["fintech"],
      excluded_industries: ["retail_pos"],
      adjacent_industries: { data_tooling: ["ai_infrastructure"] },
      size_band_min: 10,
      size_band_max: 500,
      target_funding_stages: ["series_a", "series_b"],
      target_technologies: ["kubernetes", "pytorch", "triton"],
      persona_titles: ["VP of Sales", "Head of Sales", "VP of Revenue"],
      min_score: 40,
      min_confidence: 0.6,
    });
  });

  it("Live Mode sends only the controls the form actually exposes", () => {
    expect(buildIcpOverrides("live", FORM)).toEqual({
      target_industries: ["fintech"],
      size_band_min: 10,
      size_band_max: 500,
      min_score: 40,
    });
  });

  it("Live Mode never inherits any fixture-only field", () => {
    const liveOverrides = buildIcpOverrides("live", FORM) as Record<string, unknown>;
    for (const fixtureOnlyField of [
      "target_funding_stages",
      "target_technologies",
      "persona_titles",
      "excluded_industries",
      "adjacent_industries",
      "min_confidence",
    ]) {
      expect(liveOverrides).not.toHaveProperty(fixtureOnlyField);
    }
  });
});
