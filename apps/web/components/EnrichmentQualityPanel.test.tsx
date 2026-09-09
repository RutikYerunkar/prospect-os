import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EnrichmentQualityPanel } from "@/components/EnrichmentQualityPanel";
import type { EnrichmentMetrics } from "@/lib/types";

function metrics(overrides: Partial<EnrichmentMetrics> = {}): EnrichmentMetrics {
  return {
    attempted: 0,
    matched: 0,
    match_rate: null,
    email_found_rate: null,
    email_verified_rate: null,
    catch_all_rate: null,
    linkedin_resolved_rate: null,
    identity_match_distribution: {},
    identifier_grammar_rejections: 0,
    provider_error_rate: null,
    not_attempted_budget_count: 0,
    enrichment_attempts_by_status: {},
    stale_channel_count: 0,
    preserved_last_known_good_count: 0,
    preserved_last_known_good_breakdown: {},
    p50_enrichment_latency_ms: null,
    p95_enrichment_latency_ms: null,
    enrichment_credits_used: null,
    enrichment_cost_usd: null,
    enrichment_calls: 0,
    ...overrides,
  };
}

describe("EnrichmentQualityPanel", () => {
  it("renders an explicit empty state when nothing was attempted", () => {
    const html = renderToStaticMarkup(<EnrichmentQualityPanel enrichment={metrics()} />);
    expect(html).toContain("No contact enrichment has been attempted");
  });

  it("renders None rates as em dash, never 0%", () => {
    const html = renderToStaticMarkup(
      <EnrichmentQualityPanel enrichment={metrics({ attempted: 1, matched: 0, match_rate: 0 })} />,
    );
    // match_rate=0 (a real 0%, not null) renders as 0%, not —.
    expect(html).toContain("0%");
    // email_verified_rate stayed null (no FOUND denominator) -> em dash, never "0%" for that field.
    expect(html).toContain("—");
  });

  it("never coerces a null rate to 0% anywhere in the grid", () => {
    const html = renderToStaticMarkup(
      <EnrichmentQualityPanel enrichment={metrics({ attempted: 3, matched: 1, match_rate: 1 / 3 })} />,
    );
    // catch_all_rate/email_verified_rate/linkedin_resolved_rate are all
    // still null in this fixture -> each renders as em dash.
    const dashCount = (html.match(/—/g) || []).length;
    expect(dashCount).toBeGreaterThan(0);
  });

  it("renders an unknown future identity-match state without crashing", () => {
    const html = renderToStaticMarkup(
      <EnrichmentQualityPanel
        enrichment={metrics({ attempted: 1, identity_match_distribution: { SOME_FUTURE_STATE: 2 } })}
      />,
    );
    expect(html).toContain("SOME_FUTURE_STATE");
    expect(html).toContain("2");
  });

  it("preserves the five contact axes as distinct labeled cells, not one collapsed status", () => {
    const html = renderToStaticMarkup(<EnrichmentQualityPanel enrichment={metrics({ attempted: 5 })} />);
    expect(html).toContain("Match rate");
    expect(html).toContain("Email found");
    expect(html).toContain("Email verified");
    expect(html).toContain("LinkedIn resolved");
  });

  it("shows the budget-skipped count even when nothing was actually attempted", () => {
    const html = renderToStaticMarkup(
      <EnrichmentQualityPanel enrichment={metrics({ attempted: 0, not_attempted_budget_count: 2 })} />,
    );
    expect(html).not.toContain("No contact enrichment has been attempted");
    expect(html).toContain("Budget-skipped");
  });
});
