import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ActionApprovalPanel, reasonCopy } from "@/components/ActionApprovalPanel";
import type { OutreachDraft, ProspectAggregate } from "@/lib/types";

function draft(overrides: Partial<OutreachDraft> & { id: string; channel: string }): OutreachDraft {
  return {
    step_index: 0,
    subject: "Hello",
    body: "Body text.",
    claim_map: [],
    version: 1,
    status: "DRAFT",
    content_hash: null,
    hash_version: "v1",
    ...overrides,
  };
}

function prospect(overrides: Partial<ProspectAggregate> = {}): ProspectAggregate {
  return {
    id: "p1",
    run_id: "r1",
    company: { display_name: "Northwind Labs", canonical_domain: "northwindlabs.com" },
    dedupe_key: "northwindlabs.com",
    duplicate_of: null,
    stage: "DONE",
    status: "PASS",
    error: null,
    evidence: [],
    signals: [],
    score: null,
    contact: { full_name: "Priya Natarajan", title: "VP of Sales", persona: true, linkedin_url: null, email: null, verification: "VERIFIED", evidence_ids: [] },
    drafts: [],
    review: null,
    trace: [],
    approval: { state: "PENDING", actor: null, reason: null, decided_at: null },
    contact_channels: [],
    ...overrides,
  };
}

describe("ActionApprovalPanel", () => {
  it("renders the empty state when there are no drafts", () => {
    const html = renderToStaticMarkup(<ActionApprovalPanel prospect={prospect()} />);
    expect(html).toContain("No outreach was drafted");
  });

  it("shows a Propose action button for a draft with no proposal yet", () => {
    const p = prospect({ drafts: [draft({ id: "em-1", channel: "email" })] });
    const html = renderToStaticMarkup(<ActionApprovalPanel prospect={p} />);
    expect(html).toContain("Propose action");
    // No override affordance exists anywhere before a proposal even exists.
    expect(html).not.toContain("Approve");
    expect(html).not.toContain("Execute");
  });

  it("never renders an href — no demo:// value can ever appear in one (criterion 11)", () => {
    const p = prospect({
      drafts: [
        draft({ id: "li-1", channel: "linkedin", subject: null, body: "demo://linkedin/priya-natarajan" }),
        draft({ id: "em-1", channel: "email" }),
      ],
    });
    const html = renderToStaticMarkup(<ActionApprovalPanel prospect={p} />);
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("href=");
  });

  it("renders both an email and a linkedin action card, each with its own action type label", () => {
    const p = prospect({
      drafts: [draft({ id: "em-1", channel: "email" }), draft({ id: "li-1", channel: "linkedin", subject: null })],
    });
    const html = renderToStaticMarkup(<ActionApprovalPanel prospect={p} />);
    expect(html).toContain("EMAIL_SEND");
    expect(html).toContain("LINKEDIN_COPY_AND_OPEN");
  });
});

describe("ActionApprovalPanel — V2-I-a recipient_suppressed blocked-reason copy", () => {
  it("has dedicated, provider-neutral copy for recipient_suppressed", () => {
    const copy = reasonCopy("recipient_suppressed");
    expect(copy).not.toBe("recipient_suppressed"); // not the unmapped-reason fallback
    expect(copy.toLowerCase()).toContain("privacy/legal restriction");
  });

  it("never uses forbidden human-intent wording in ANY blocked-reason copy", () => {
    const knownReasons = [
      "review_not_passed",
      "prospect_not_actionable",
      "email_not_discovered",
      "email_not_verified",
      "contact_state_stale",
      "draft_incomplete",
      "recipient_identity_invalid",
      "content_changed",
      "approval_superseded",
      "sender_not_connected",
      "sender_changed",
      "already_sent_to_recipient",
      "prior_send_uncertain",
      "send_in_flight",
      "send_provider_unavailable",
      "send_allowance_exhausted",
      "demo_action_cap_reached",
      "linkedin_not_resolved",
      "linkedin_identity_not_strong",
      "recipient_suppressed",
    ];
    const forbidden = ["withdrew", "consent", "claimed by its owner"];
    for (const reason of knownReasons) {
      const copy = reasonCopy(reason).toLowerCase();
      for (const word of forbidden) {
        expect(copy).not.toContain(word);
      }
    }
  });

  it("falls back to the raw reason string for an unrecognized code", () => {
    expect(reasonCopy("some_future_reason")).toBe("some_future_reason");
  });
});
