import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ActionGovernancePanel } from "@/components/ActionGovernancePanel";
import type { ActionMetrics } from "@/lib/types";

function metrics(overrides: Partial<ActionMetrics> = {}): ActionMetrics {
  return {
    proposals_by_verdict: {},
    blocked_reasons: {},
    execution_blocked_reasons: {},
    execution_blocked_attempts: 0,
    execution_blocked_proposals: 0,
    content_hash_mismatch_count: 0,
    approval_to_execution_latency_p50_ms: null,
    approval_to_execution_latency_p95_ms: null,
    executions_by_status: {},
    executions_by_origin: {},
    uncertain_count: 0,
    reconciliation_outcomes: {},
    mean_messages_scanned_per_reconcile: null,
    cross_run_recipient_blocks: 0,
    cross_run_recipient_blocked_proposals: 0,
    ...overrides,
  };
}

describe("ActionGovernancePanel", () => {
  it("explicitly communicates that no governed action has been proposed yet — never a wall of zeros", () => {
    const html = renderToStaticMarkup(<ActionGovernancePanel actions={metrics()} />);
    expect(html).toContain("No governed action has been proposed for this run yet");
    // The empty state must not also render the full zeroed metric grid.
    expect(html).not.toContain("Execution-blocked attempts");
  });

  it("renders proposal counts and verdict badges once at least one proposal exists", () => {
    const html = renderToStaticMarkup(
      <ActionGovernancePanel actions={metrics({ proposals_by_verdict: { ELIGIBLE: 2, BLOCKED: 1 } })} />,
    );
    expect(html).toContain("ELIGIBLE");
    expect(html).toContain("BLOCKED");
    expect(html).toContain("Proposals");
  });

  it("renders an unknown future blocked reason without crashing (generic reason vocabulary)", () => {
    const html = renderToStaticMarkup(
      <ActionGovernancePanel
        actions={metrics({
          proposals_by_verdict: { BLOCKED: 1 },
          execution_blocked_reasons: { some_future_reason_code: 3 },
        })}
      />,
    );
    expect(html).toContain("some_future_reason_code");
    expect(html).toContain("3");
  });

  it("keeps proposal-time and execution-time blocked reasons in visibly separate sections", () => {
    const html = renderToStaticMarkup(
      <ActionGovernancePanel
        actions={metrics({
          proposals_by_verdict: { BLOCKED: 1, ELIGIBLE: 1 },
          blocked_reasons: { email_not_verified: 1 },
          execution_blocked_reasons: { content_changed: 1 },
        })}
      />,
    );
    const proposalHeadingIndex = html.indexOf("Proposal-time blocked reasons");
    const executionHeadingIndex = html.indexOf("Execution-time blocked reasons");
    expect(proposalHeadingIndex).toBeGreaterThan(-1);
    expect(executionHeadingIndex).toBeGreaterThan(proposalHeadingIndex);
    expect(html.indexOf("email_not_verified")).toBeLessThan(executionHeadingIndex);
    expect(html.indexOf("content_changed")).toBeGreaterThan(executionHeadingIndex);
  });

  it("renders cross-run recipient block counters", () => {
    const html = renderToStaticMarkup(
      <ActionGovernancePanel
        actions={metrics({
          proposals_by_verdict: { BLOCKED: 1 },
          cross_run_recipient_blocks: 2,
          cross_run_recipient_blocked_proposals: 1,
        })}
      />,
    );
    expect(html).toContain("Cross-run recipient blocks");
    expect(html).toContain("Cross-run blocked proposals");
  });
});
