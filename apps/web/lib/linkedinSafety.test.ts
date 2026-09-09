import { describe, expect, it } from "vitest";
import { isSafeLinkedInHref, isSafeLinkedInProfileUrl } from "@/lib/linkedinSafety";

describe("isSafeLinkedInProfileUrl", () => {
  it("accepts the apex-domain canonical case", () => {
    expect(isSafeLinkedInProfileUrl("https://linkedin.com/in/priya-natarajan")).toBe(true);
  });

  it("accepts the www canonical case", () => {
    expect(isSafeLinkedInProfileUrl("https://www.linkedin.com/in/priya-natarajan")).toBe(true);
  });

  it("accepts an optional trailing slash on the path", () => {
    expect(isSafeLinkedInProfileUrl("https://www.linkedin.com/in/priya-natarajan/")).toBe(true);
  });

  it("rejects a demo:// identifier", () => {
    expect(isSafeLinkedInProfileUrl("demo://linkedin/priya-natarajan")).toBe(false);
  });

  it("rejects a plain http URL", () => {
    expect(isSafeLinkedInProfileUrl("http://linkedin.com/in/priya-natarajan")).toBe(false);
  });

  it("rejects a lookalike host (linkedin.com as a subdomain of an attacker domain)", () => {
    expect(isSafeLinkedInProfileUrl("https://linkedin.com.evil.com/in/priya-natarajan")).toBe(false);
  });

  it("rejects a non-linkedin host entirely", () => {
    expect(isSafeLinkedInProfileUrl("https://notlinkedin.com/in/priya-natarajan")).toBe(false);
  });

  it("rejects a different TLD", () => {
    expect(isSafeLinkedInProfileUrl("https://linkedin.co/in/priya-natarajan")).toBe(false);
  });

  it("rejects a wrong path", () => {
    expect(isSafeLinkedInProfileUrl("https://linkedin.com/company/priya-natarajan")).toBe(false);
  });

  it("rejects userinfo in the URL", () => {
    expect(isSafeLinkedInProfileUrl("https://user:pass@linkedin.com/in/priya-natarajan")).toBe(false);
  });

  it("rejects an explicit port, including the default port", () => {
    expect(isSafeLinkedInProfileUrl("https://linkedin.com:443/in/priya-natarajan")).toBe(false);
    expect(isSafeLinkedInProfileUrl("https://linkedin.com:8443/in/priya-natarajan")).toBe(false);
  });

  it("rejects a fragment", () => {
    expect(isSafeLinkedInProfileUrl("https://linkedin.com/in/priya-natarajan#section")).toBe(false);
  });

  it("rejects an unsupported scheme", () => {
    expect(isSafeLinkedInProfileUrl("javascript:alert(1)")).toBe(false);
    expect(isSafeLinkedInProfileUrl("ftp://linkedin.com/in/priya-natarajan")).toBe(false);
  });

  it("rejects a malformed URL", () => {
    expect(isSafeLinkedInProfileUrl("not a url at all")).toBe(false);
    expect(isSafeLinkedInProfileUrl("")).toBe(false);
    expect(isSafeLinkedInProfileUrl(null)).toBe(false);
    expect(isSafeLinkedInProfileUrl(undefined)).toBe(false);
  });

  it("rejects a backslash-smuggled authority", () => {
    expect(isSafeLinkedInProfileUrl("https://evil.com\\@linkedin.com/in/priya-natarajan")).toBe(false);
  });

  it("rejects an overlength URL", () => {
    const long = "https://linkedin.com/in/" + "a".repeat(3000);
    expect(isSafeLinkedInProfileUrl(long)).toBe(false);
  });

  it("accepts a case-insensitive https scheme", () => {
    expect(isSafeLinkedInProfileUrl("HTTPS://linkedin.com/in/priya-natarajan")).toBe(true);
  });
});

describe("isSafeLinkedInHref — full predicate (channel + origin + state + URL)", () => {
  const validUrl = "https://www.linkedin.com/in/priya-natarajan";

  it("accepts only channel=linkedin + origin=LIVE_PROVIDER + state=RESOLVED + a safe URL", () => {
    expect(
      isSafeLinkedInHref({ channel: "linkedin", origin: "LIVE_PROVIDER", discoveryState: "RESOLVED", identifier: validUrl }),
    ).toBe(true);
  });

  it("rejects a DEMO_FIXTURE origin even with a RESOLVED state and a same-shaped URL", () => {
    expect(
      isSafeLinkedInHref({ channel: "linkedin", origin: "DEMO_FIXTURE", discoveryState: "RESOLVED", identifier: validUrl }),
    ).toBe(false);
  });

  it("rejects a demo:// identifier even when everything else claims RESOLVED/LIVE_PROVIDER", () => {
    expect(
      isSafeLinkedInHref({
        channel: "linkedin",
        origin: "LIVE_PROVIDER",
        discoveryState: "RESOLVED",
        identifier: "demo://linkedin/priya-natarajan",
      }),
    ).toBe(false);
  });

  it("rejects a non-RESOLVED discovery state", () => {
    expect(
      isSafeLinkedInHref({ channel: "linkedin", origin: "LIVE_PROVIDER", discoveryState: "NOT_FOUND", identifier: validUrl }),
    ).toBe(false);
  });

  it("rejects the email channel even with a linkedin-shaped identifier", () => {
    expect(
      isSafeLinkedInHref({ channel: "email", origin: "LIVE_PROVIDER", discoveryState: "RESOLVED", identifier: validUrl }),
    ).toBe(false);
  });

  it("rejects a malicious URL even when LIVE_PROVIDER + RESOLVED both claim it's safe", () => {
    expect(
      isSafeLinkedInHref({
        channel: "linkedin",
        origin: "LIVE_PROVIDER",
        discoveryState: "RESOLVED",
        identifier: "https://linkedin.com.evil.com/in/priya-natarajan",
      }),
    ).toBe(false);
  });

  it("rejects a missing identifier", () => {
    expect(
      isSafeLinkedInHref({ channel: "linkedin", origin: "LIVE_PROVIDER", discoveryState: "RESOLVED", identifier: null }),
    ).toBe(false);
  });
});
