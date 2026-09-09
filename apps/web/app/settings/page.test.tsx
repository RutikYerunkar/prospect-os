import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import SettingsPage from "@/app/settings/page";

// `SettingsPage` is an async Server Component — calling it directly returns
// a Promise of the element tree, exactly as Next's own renderer would await
// it. `renderToStaticMarkup` never runs effects (SSR's render phase only),
// so `SettingsClient`'s `useEffect`s (the provider-settings fetch and the
// post-hydration URL cleanup, both of which touch `window`) never execute
// here — this suite runs under vitest's `environment: "node"`, which has NO
// `window`/`document` global at all, so any accidental effect execution (or
// any render-time read of a browser global) would throw immediately rather
// than silently pass.
async function renderSettingsPage(searchParams: Record<string, string | string[]>): Promise<string> {
  const element = await SettingsPage({ searchParams: Promise.resolve(searchParams) });
  return renderToStaticMarkup(element);
}

describe("SettingsPage — server-derived initial Gmail banner", () => {
  it("?gmail=connected renders the connected banner in the FIRST render, deterministically", async () => {
    const html = await renderSettingsPage({ gmail: "connected" });
    expect(html).toContain("Gmail connected.");
  });

  it("?gmail=error&reason=access_denied renders the sanitized error banner in the FIRST render", async () => {
    const html = await renderSettingsPage({ gmail: "error", reason: "access_denied" });
    expect(html).toContain("Gmail connection failed");
    expect(html).toContain("access_denied");
  });

  it("never reflects arbitrary/unrecognized error text into the rendered markup", async () => {
    const html = await renderSettingsPage({ gmail: "error", reason: "<script>alert(1)</script>" });
    expect(html).not.toContain("<script>alert(1)</script>");
    expect(html).toContain("unknown");
  });

  it("renders no banner at all when there is no gmail query parameter", async () => {
    const html = await renderSettingsPage({});
    expect(html).not.toContain("Gmail connected.");
    expect(html).not.toContain("Gmail connection failed");
  });

  it("rendering does not throw in an environment with no window/document global", async () => {
    // The mere fact that these calls complete without a ReferenceError
    // (this test file's environment defines neither `window` nor
    // `document`) proves the server render path — page.tsx ->
    // deriveGmailBanner -> SettingsClient's first render ->
    // GmailSettingsPanel — never touches a browser global to decide what
    // to render. A throw here would fail the test on its own.
    expect(await renderSettingsPage({ gmail: "connected" })).toEqual(expect.any(String));
    expect(await renderSettingsPage({ gmail: "error", reason: "access_denied" })).toEqual(expect.any(String));
    expect(await renderSettingsPage({})).toEqual(expect.any(String));
  });
});
