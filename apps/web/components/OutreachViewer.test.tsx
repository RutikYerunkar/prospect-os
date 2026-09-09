import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { OutreachViewer } from "@/components/OutreachViewer";
import type { EvidenceItem, OutreachDraft } from "@/lib/types";

const NO_EVIDENCE: Record<string, EvidenceItem> = {};

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

describe("OutreachViewer", () => {
  it("renders the empty state when there are no drafts", () => {
    const html = renderToStaticMarkup(<OutreachViewer drafts={[]} evidenceById={NO_EVIDENCE} />);
    expect(html).toContain("No outreach was drafted");
  });

  it("groups drafts by channel with EMAIL first, regardless of input order", () => {
    const drafts: OutreachDraft[] = [
      draft({ id: "li-1", channel: "linkedin", subject: null, step_index: 1, body: "LinkedIn body." }),
      draft({ id: "em-1", channel: "email", subject: "Subject line", step_index: 0, body: "Email body." }),
    ];
    const html = renderToStaticMarkup(<OutreachViewer drafts={drafts} evidenceById={NO_EVIDENCE} />);
    const emailHeadingIndex = html.indexOf(">email<");
    const linkedinHeadingIndex = html.indexOf(">linkedin<");
    expect(emailHeadingIndex).toBeGreaterThan(-1);
    expect(linkedinHeadingIndex).toBeGreaterThan(-1);
    expect(emailHeadingIndex).toBeLessThan(linkedinHeadingIndex);
  });

  it("orders drafts within a channel group by step_index", () => {
    const drafts: OutreachDraft[] = [
      draft({ id: "em-2", channel: "email", step_index: 2, body: "Second." }),
      draft({ id: "em-1", channel: "email", step_index: 0, body: "First." }),
    ];
    const html = renderToStaticMarkup(<OutreachViewer drafts={drafts} evidenceById={NO_EVIDENCE} />);
    expect(html.indexOf("First.")).toBeLessThan(html.indexOf("Second."));
  });

  it("renders a subject-less LinkedIn draft correctly, without a subject paragraph", () => {
    const drafts: OutreachDraft[] = [
      draft({ id: "li-1", channel: "linkedin", subject: null, body: "Hi there — congrats on the round." }),
    ];
    const html = renderToStaticMarkup(<OutreachViewer drafts={drafts} evidenceById={NO_EVIDENCE} />);
    expect(html).toContain("Hi there — congrats on the round.");
    expect(html).not.toContain("<p class=\"mt-2");
  });

  it("never emits an href — no unsafe href, no demo:// href, no LinkedIn identifier href", () => {
    const drafts: OutreachDraft[] = [
      draft({
        id: "li-1", channel: "linkedin", subject: null,
        body: "Connect with me: https://www.linkedin.com/in/jane-doe or demo://linkedin/jane-doe",
      }),
    ];
    const html = renderToStaticMarkup(<OutreachViewer drafts={drafts} evidenceById={NO_EVIDENCE} />);
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("href=");
  });

  it("still renders an email draft's subject normally", () => {
    const drafts: OutreachDraft[] = [draft({ id: "em-1", channel: "email", subject: "Congrats, Acme" })];
    const html = renderToStaticMarkup(<OutreachViewer drafts={drafts} evidenceById={NO_EVIDENCE} />);
    expect(html).toContain("Congrats, Acme");
  });

  it("groups an unrecognized future channel after the known ones without crashing", () => {
    const drafts: OutreachDraft[] = [
      draft({ id: "sms-1", channel: "sms", subject: null, body: "SMS body." }),
      draft({ id: "em-1", channel: "email", subject: "Subject", body: "Email body." }),
    ];
    const html = renderToStaticMarkup(<OutreachViewer drafts={drafts} evidenceById={NO_EVIDENCE} />);
    expect(html.indexOf(">email<")).toBeLessThan(html.indexOf(">sms<"));
  });
});
