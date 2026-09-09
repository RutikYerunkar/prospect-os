import { describe, expect, it } from "vitest";
import { deriveGmailBanner } from "@/lib/gmailBanner";

// This suite runs under vitest's `environment: "node"` (see
// vitest.config.mts) — there is no `window`/`document` global at all here.
// If `deriveGmailBanner` touched one, every test below would throw a
// ReferenceError before any assertion ran. That absence of a crash is
// itself the proof that the derivation depends only on its plain-object
// argument, never a browser global.

describe("deriveGmailBanner", () => {
  it("produces a connected banner for ?gmail=connected", () => {
    expect(deriveGmailBanner({ gmail: "connected" })).toEqual({ kind: "connected" });
  });

  it("produces a sanitized error banner for an allow-listed reason", () => {
    expect(deriveGmailBanner({ gmail: "error", reason: "access_denied" })).toEqual({
      kind: "error",
      reason: "access_denied",
    });
  });

  it("never reflects an arbitrary/unrecognized reason verbatim", () => {
    expect(deriveGmailBanner({ gmail: "error", reason: "<script>alert(1)</script>" })).toEqual({
      kind: "error",
      reason: "unknown",
    });
    expect(deriveGmailBanner({ gmail: "error", reason: "totally_made_up_code" })).toEqual({
      kind: "error",
      reason: "unknown",
    });
  });

  it("defaults to 'unknown' when gmail=error carries no reason at all", () => {
    expect(deriveGmailBanner({ gmail: "error" })).toEqual({ kind: "error", reason: "unknown" });
  });

  it("produces no banner when there is no gmail query parameter", () => {
    expect(deriveGmailBanner({})).toBeNull();
  });

  it("produces no banner for an unrecognized gmail value", () => {
    expect(deriveGmailBanner({ gmail: "something-else" })).toBeNull();
  });

  it("takes the first value when Next hands back an array (repeated query key)", () => {
    expect(deriveGmailBanner({ gmail: ["connected", "error"] })).toEqual({ kind: "connected" });
    expect(deriveGmailBanner({ gmail: "error", reason: ["access_denied", "unknown"] })).toEqual({
      kind: "error",
      reason: "access_denied",
    });
  });

  it("is deterministic — repeated calls with the same input produce the same output", () => {
    const input = { gmail: "error" as const, reason: "access_denied" };
    const first = deriveGmailBanner(input);
    const second = deriveGmailBanner(input);
    expect(first).toEqual(second);
  });
});
