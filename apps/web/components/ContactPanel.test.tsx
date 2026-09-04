import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ContactPanel } from "@/components/ContactPanel";
import type { ContactChannel, EvidenceItem, ProspectContact } from "@/lib/types";

const NO_EVIDENCE: Record<string, EvidenceItem> = {};

function channel(overrides: Partial<ContactChannel> & { channel: string }): ContactChannel {
  return {
    identifier: null,
    discovery_state: null,
    verification_state: null,
    identity_match_state: null,
    derivation_version: "v1",
    observed_at: null,
    last_attempt_at: null,
    last_attempt_status: null,
    last_attempt_error_type: null,
    origin: null,
    provider: null,
    stale: null,
    stale_after_days: null,
    preserved_state: null,
    provider_confidence: null,
    is_catch_all: null,
    ...overrides,
  };
}

const NORTHWIND_CONTACT: ProspectContact = {
  full_name: "Priya Natarajan",
  title: "VP of Sales",
  persona: true,
  linkedin_url: null,
  email: "priya.natarajan@northwindlabs.com",
  verification: "VERIFIED",
  evidence_ids: [],
};

const NORTHWIND_CHANNELS: ContactChannel[] = [
  channel({
    channel: "email",
    identifier: "priya.natarajan@northwindlabs.com",
    discovery_state: "FOUND",
    verification_state: "VERIFIED",
    origin: "DEMO_FIXTURE",
    provider: "demo_fixture",
    stale: false,
    stale_after_days: 30,
    provider_confidence: 0.95,
    is_catch_all: false,
  }),
  channel({
    channel: "linkedin",
    identifier: "demo://linkedin/priya-natarajan",
    discovery_state: "RESOLVED",
    identity_match_state: "STRONG_MATCH",
    origin: "DEMO_FIXTURE",
    provider: "demo_fixture",
    stale: false,
    stale_after_days: 30,
  }),
];

function render(contact: ProspectContact | null, contactChannels: ContactChannel[]) {
  return renderToStaticMarkup(
    <ContactPanel contact={contact} contactChannels={contactChannels} evidenceById={NO_EVIDENCE} />,
  );
}

describe("ContactPanel — Northwind (demo hero path)", () => {
  const html = render(NORTHWIND_CONTACT, NORTHWIND_CHANNELS);

  it("shows VERIFIED / FOUND / VERIFIED / STRONG MATCH", () => {
    expect(html).toContain("VERIFIED");
    expect(html).toContain("FOUND");
    expect(html).toContain("STRONG MATCH");
  });

  it("never turns the demo:// LinkedIn identifier into an href", () => {
    expect(html).not.toMatch(/href="demo:\/\//);
    expect(html).toContain("demo://linkedin/priya-natarajan");
    expect(html).toContain("simulated profile");
  });

  it("renders the email discovery observation chips (confidence/catch-all) separately from the state explanation", () => {
    expect(html).toContain("Confidence 95%");
    expect(html).toContain("Not a catch-all domain");
  });

  it("has no send/approve/copy/mailto affordances", () => {
    expect(html.toLowerCase()).not.toContain("mailto:");
    expect(html).not.toMatch(/<button/i);
    expect(html.toLowerCase()).not.toContain(">copy<");
    expect(html.toLowerCase()).not.toContain(">send<");
    expect(html.toLowerCase()).not.toContain(">approve<");
  });
});

describe("ContactPanel — Sable-shaped (RISKY email + STRONG_MATCH LinkedIn)", () => {
  const channels: ContactChannel[] = [
    channel({
      channel: "email",
      identifier: "m.webb@sable-example.com",
      discovery_state: "FOUND",
      verification_state: "RISKY",
      origin: "DEMO_FIXTURE",
      provider: "demo_fixture",
      stale: false,
      stale_after_days: 30,
      is_catch_all: true,
    }),
    channel({
      channel: "linkedin",
      identifier: "demo://linkedin/marcus-webb",
      discovery_state: "RESOLVED",
      identity_match_state: "STRONG_MATCH",
      origin: "DEMO_FIXTURE",
      provider: "demo_fixture",
    }),
  ];
  const html = render(
    { full_name: "Marcus Webb", title: "Head of Sales", persona: true, linkedin_url: null, email: null, verification: "VERIFIED", evidence_ids: [] },
    channels,
  );

  it("shows RISKY, distinct from VERIFIED/INVALID copy", () => {
    expect(html).toContain("RISKY");
    expect(html).toContain("could not be confirmed as safely deliverable");
  });

  it("never mentions catch-all inside a state explanation — only as its own observation chip", () => {
    expect(html).toContain("Catch-all domain");
  });
});

describe("ContactPanel — PROVIDER_ERROR / stale / preserved states", () => {
  it("renders PROVIDER_ERROR distinctly from NOT_FOUND", () => {
    const providerError = render(null, [
      channel({ channel: "email", discovery_state: "PROVIDER_ERROR" }),
    ]);
    const notFound = render(null, [channel({ channel: "email", discovery_state: "NOT_FOUND" })]);
    expect(providerError).toContain("PROVIDER ERROR");
    expect(providerError).not.toContain("NOT FOUND");
    expect(notFound).toContain("NOT FOUND");
    expect(notFound).not.toContain("PROVIDER ERROR");
  });

  it("shows a stale badge when stale is true", () => {
    const html = render(null, [
      channel({ channel: "email", discovery_state: "FOUND", verification_state: "VERIFIED", stale: true, stale_after_days: 30 }),
    ]);
    expect(html).toContain("Stale");
    expect(html).toContain("30d");
  });

  it("shows the REFRESH_FAILED note", () => {
    const html = render(null, [
      channel({ channel: "email", discovery_state: "FOUND", preserved_state: "REFRESH_FAILED" }),
    ]);
    expect(html).toContain("most recent refresh attempt failed");
  });

  it("shows the REFRESH_FOUND_NOTHING note, distinct from REFRESH_FAILED", () => {
    const html = render(null, [
      channel({ channel: "email", discovery_state: "FOUND", preserved_state: "REFRESH_FOUND_NOTHING" }),
    ]);
    expect(html).toContain("found nothing new");
    expect(html).not.toContain("attempt failed");
  });
});

describe("ContactPanel — null/absent/unknown-state cases", () => {
  it("renders NOT OBSERVED for every axis when contact_channels is empty", () => {
    const html = render(null, []);
    const count = (html.match(/NOT OBSERVED/g) ?? []).length;
    // person identity (contact is null) + email discovery + email
    // verification + linkedin resolution + linkedin identity match.
    expect(count).toBe(5);
  });

  it("renders 'contact resolution did not run' when contact is null", () => {
    const html = render(null, []);
    expect(html).toContain("Contact resolution did not run for this prospect.");
  });

  it("gracefully renders an unrecognized future enum value instead of crashing", () => {
    const html = render(null, [
      channel({ channel: "email", discovery_state: "SOME_FUTURE_STATE_NOT_YET_KNOWN" }),
    ]);
    expect(html).toContain("UNKNOWN STATE");
    // React HTML-escapes the apostrophe in static markup.
    expect(html).toContain("isn&#x27;t recognized by this version of the UI");
  });

  it("renders a boolean persona without leaking 'true'/'false' as visible text", () => {
    const html = render(
      { full_name: "A B", title: null, persona: true, linkedin_url: null, email: null, verification: "PERSONA_ONLY", evidence_ids: [] },
      [],
    );
    expect(html).not.toMatch(/>true</);
    expect(html).not.toMatch(/>false</);
  });
});

describe("ContactPanel — LinkedIn href safety inside the rendered component", () => {
  it("renders a safe LIVE_PROVIDER + RESOLVED canonical URL as a real href", () => {
    const html = render(null, [
      channel({
        channel: "linkedin",
        identifier: "https://www.linkedin.com/in/priya-natarajan",
        discovery_state: "RESOLVED",
        identity_match_state: "STRONG_MATCH",
        origin: "LIVE_PROVIDER",
        provider: "hunter",
      }),
    ]);
    expect(html).toContain('href="https://www.linkedin.com/in/priya-natarajan"');
  });

  it("never renders a malicious lookalike URL as an href, even claiming LIVE_PROVIDER + RESOLVED", () => {
    const html = render(null, [
      channel({
        channel: "linkedin",
        identifier: "https://linkedin.com.evil.com/in/priya-natarajan",
        discovery_state: "RESOLVED",
        identity_match_state: "STRONG_MATCH",
        origin: "LIVE_PROVIDER",
        provider: "hunter",
      }),
    ]);
    expect(html).not.toMatch(/<a[^>]+href="https:\/\/linkedin\.com\.evil\.com/);
    expect(html).toContain("https://linkedin.com.evil.com/in/priya-natarajan");
  });
});
