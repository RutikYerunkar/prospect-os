import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ActionAuditPanel, outcomeCopy } from "@/components/ActionAuditPanel";

describe("ActionAuditPanel — exact required outcome copy", () => {
  it("SUCCEEDED reads 'Gmail accepted this message' — never 'delivered'", () => {
    const copy = outcomeCopy("SUCCEEDED");
    expect(copy).toBe("Gmail accepted this message");
    expect(copy?.toLowerCase()).not.toContain("delivered");
  });

  it("UNCERTAIN reads the exact required acceptance-not-established copy", () => {
    expect(outcomeCopy("UNCERTAIN")).toBe("acceptance not established — Groundwork will not resend");
  });

  it("ABANDONED reads the exact required not-evidence-of-non-delivery copy", () => {
    expect(outcomeCopy("ABANDONED")).toBe("stopped checking; this is not evidence the message was not sent");
  });

  it("a 403/429/5xx-derived UNCERTAIN status is never called 'rejected'", () => {
    const copy = outcomeCopy("UNCERTAIN")!.toLowerCase();
    expect(copy).not.toContain("rejected");
  });

  it("CLAIMED/IN_FLIGHT have no special copy (null)", () => {
    expect(outcomeCopy("CLAIMED")).toBeNull();
    expect(outcomeCopy("IN_FLIGHT")).toBeNull();
  });

  it("EMAIL_SEND (explicit action_type) still reads the exact required Gmail-accepted copy", () => {
    expect(outcomeCopy("SUCCEEDED", "EMAIL_SEND")).toBe("Gmail accepted this message");
  });
});

describe("ActionAuditPanel — V2-I-c: LINKEDIN_COPY_AND_OPEN outcome copy is action-type-aware", () => {
  it("SUCCEEDED LinkedIn copy never mentions Gmail", () => {
    const copy = outcomeCopy("SUCCEEDED", "LINKEDIN_COPY_AND_OPEN");
    expect(copy).not.toBeNull();
    expect(copy!.toLowerCase()).not.toContain("gmail");
  });

  it("SUCCEEDED LinkedIn copy never claims a message was sent, LinkedIn was contacted, or a network request occurred", () => {
    const copy = outcomeCopy("SUCCEEDED", "LINKEDIN_COPY_AND_OPEN")!.toLowerCase();
    // Never claims dispatch/send activity.
    expect(copy).not.toContain("accepted this message");
    expect(copy).not.toContain("delivered");
    expect(copy).not.toContain("dispatched");
    // Never claims LinkedIn itself was contacted/reached.
    expect(copy).not.toContain("contacted linkedin");
    expect(copy).not.toContain("sent to linkedin");
    // Never claims a network request happened.
    expect(copy).not.toContain("network request");
  });

  it("SUCCEEDED LinkedIn copy never claims the profile was opened — that's a separate operator-clicked affordance", () => {
    const copy = outcomeCopy("SUCCEEDED", "LINKEDIN_COPY_AND_OPEN")!.toLowerCase();
    expect(copy).not.toContain("opened the profile");
    expect(copy).not.toContain("profile was opened");
  });

  it("SUCCEEDED LinkedIn copy differs from the SUCCEEDED EMAIL_SEND copy", () => {
    expect(outcomeCopy("SUCCEEDED", "LINKEDIN_COPY_AND_OPEN")).not.toBe(outcomeCopy("SUCCEEDED", "EMAIL_SEND"));
  });

  it("omitting action_type still defaults to the EMAIL_SEND/Gmail copy (backward-compatible default)", () => {
    expect(outcomeCopy("SUCCEEDED")).toBe("Gmail accepted this message");
  });
});

describe("ActionAuditPanel — initial render (no resolved fetch under SSR)", () => {
  it("renders the loading state without crashing", () => {
    const html = renderToStaticMarkup(<ActionAuditPanel proposalId="p1" />);
    expect(html).toContain("Loading audit trail");
  });
});

describe("ActionAuditPanel/ActionApprovalPanel — no resend control anywhere (D7/D10)", () => {
  // Note: the UNCERTAIN copy legitimately contains the word "resend" (the
  // prose "Groundwork will not resend") — these checks look for an actual
  // resend CONTROL (a button/handler/API call), never a bare substring.
  const resendControlPattern = /\bresend(execution|action|button|control)?\s*[(:=]/i;

  it("the audit panel source has no resend handler/button/API call", () => {
    const source = readFileSync(new URL("./ActionAuditPanel.tsx", import.meta.url), "utf-8");
    expect(source).not.toMatch(resendControlPattern);
  });

  it("the approval panel source has no resend handler/button/API call", () => {
    const source = readFileSync(new URL("./ActionApprovalPanel.tsx", import.meta.url), "utf-8");
    expect(source).not.toMatch(resendControlPattern);
  });

  it("lib/api.ts exposes no resend* function", () => {
    const source = readFileSync(new URL("../lib/api.ts", import.meta.url), "utf-8");
    expect(source).not.toMatch(/export function resend/i);
  });
});
