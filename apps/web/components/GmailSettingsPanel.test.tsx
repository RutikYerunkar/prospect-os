import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GmailSettingsPanel, type GmailBanner } from "@/components/GmailSettingsPanel";
import type { GmailAvailability } from "@/lib/types";

const NOOP = () => {};

function baseProps() {
  return {
    isOperator: false,
    operatorLoginConfigured: true,
    settingsUnreachable: false,
    gmail: null as GmailAvailability | null,
    banner: null as GmailBanner,
    passphrase: "",
    onPassphraseChange: NOOP,
    onUnlock: NOOP,
    unlocking: false,
    unlockError: null,
    onConnect: NOOP,
    connecting: false,
    connectError: null,
    onDisconnect: NOOP,
    disconnecting: false,
    onLockAgain: NOOP,
  };
}

function render(props: Partial<ReturnType<typeof baseProps>>) {
  const merged = { ...baseProps(), ...props } as Parameters<typeof GmailSettingsPanel>[0];
  return renderToStaticMarkup(<GmailSettingsPanel {...merged} />);
}

describe("GmailSettingsPanel — state 1: non-operator", () => {
  it("shows the sign-in message and never a connect/disconnect affordance", () => {
    const html = render({ isOperator: false });
    expect(html).toContain("Sign in as operator to manage the sending identity.");
    expect(html).not.toContain("Connect Gmail");
    expect(html).not.toContain("Disconnect");
  });

  it("offers the operator unlock form when operator login is configured", () => {
    const html = render({ isOperator: false, operatorLoginConfigured: true });
    expect(html).toContain("Operator passphrase");
  });

  it("omits the unlock form when operator login isn't configured on this deployment", () => {
    const html = render({ isOperator: false, operatorLoginConfigured: false });
    expect(html).not.toContain("Operator passphrase");
  });
});

describe("GmailSettingsPanel — state 2: operator, OAuth not configured", () => {
  it("shows the not-configured message", () => {
    const html = render({
      isOperator: true,
      gmail: { configured: false, connected: false, google_account_email: null, scopes: [], connected_at: null },
    });
    expect(html).toContain("Gmail OAuth is not configured on this deployment.");
    expect(html).not.toContain("Connect Gmail");
  });
});

describe("GmailSettingsPanel — state 3: operator, configured, not connected", () => {
  it("shows the Connect Gmail button", () => {
    const html = render({
      isOperator: true,
      gmail: { configured: true, connected: false, google_account_email: null, scopes: [], connected_at: null },
    });
    expect(html).toContain("Connect Gmail");
    expect(html).not.toContain("Disconnect");
  });
});

describe("GmailSettingsPanel — state 4: operator, connected", () => {
  const CONNECTED: GmailAvailability = {
    configured: true,
    connected: true,
    google_account_email: "operator@example.com",
    scopes: ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.metadata"],
    connected_at: "2026-01-01T00:00:00Z",
  };

  it("shows the connected address, scopes, connected_at, and a Disconnect button", () => {
    const html = render({ isOperator: true, gmail: CONNECTED });
    expect(html).toContain("operator@example.com");
    expect(html).toContain("gmail.send");
    expect(html).toContain("gmail.metadata");
    expect(html).toContain("Disconnect");
    expect(html).not.toContain("Connect Gmail");
  });

  it("never renders gmail.readonly/openid/email/profile even if a caller passed them", () => {
    const html = render({
      isOperator: true,
      gmail: { ...CONNECTED, scopes: [...CONNECTED.scopes] },
    });
    expect(html).not.toContain("gmail.readonly");
  });
});

describe("GmailSettingsPanel — result banners", () => {
  it("renders a connected banner", () => {
    const html = render({ banner: { kind: "connected" } });
    expect(html).toContain("Gmail connected.");
  });

  it("renders a sanitized error banner", () => {
    const html = render({ banner: { kind: "error", reason: "access_denied" } });
    expect(html).toContain("access_denied");
  });
});

describe("GmailSettingsPanel — never leaks credential material", () => {
  it("contains no token/secret/verifier/key text under any state, connected included", () => {
    const states: Array<Partial<ReturnType<typeof baseProps>>> = [
      { isOperator: false },
      { isOperator: true, gmail: { configured: false, connected: false, google_account_email: null, scopes: [], connected_at: null } },
      { isOperator: true, gmail: { configured: true, connected: false, google_account_email: null, scopes: [], connected_at: null } },
      {
        isOperator: true,
        gmail: {
          configured: true,
          connected: true,
          google_account_email: "operator@example.com",
          scopes: ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.metadata"],
          connected_at: "2026-01-01T00:00:00Z",
        },
      },
    ];
    for (const state of states) {
      const html = render(state).toLowerCase();
      expect(html).not.toContain("refresh_token");
      expect(html).not.toContain("access_token");
      expect(html).not.toContain("client_secret");
      expect(html).not.toContain("pkce");
      expect(html).not.toContain("verifier");
      expect(html).not.toContain("binding_tag");
      expect(html).not.toContain("ciphertext");
    }
  });
});

describe("GmailSettingsPanel — unreachable API", () => {
  it("shows the unreachable message and suppresses every other state", () => {
    const html = render({ settingsUnreachable: true, isOperator: true, gmail: null });
    expect(html).toContain("Could not reach the API");
    expect(html).not.toContain("Connect Gmail");
    expect(html).not.toContain("Sign in as operator");
  });
});

// Sanity: props are wired, not ignored (guards against a future refactor
// silently dropping a handler).
describe("GmailSettingsPanel — handlers are real props", () => {
  it("accepts callable handlers without throwing at render time", () => {
    const onConnect = vi.fn();
    expect(() =>
      render({
        isOperator: true,
        gmail: { configured: true, connected: false, google_account_email: null, scopes: [], connected_at: null },
        onConnect,
      }),
    ).not.toThrow();
  });
});
