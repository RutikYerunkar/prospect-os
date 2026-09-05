import { deriveGmailBanner } from "@/lib/gmailBanner";
import { SettingsClient } from "./SettingsClient";

/**
 * Server Component (the page.js leaf itself) — computes the Gmail OAuth
 * result banner from `searchParams` BEFORE any client render happens, so
 * the server-rendered HTML and the client's first render start from the
 * exact same value. `deriveGmailBanner` is pure (no browser globals); the
 * interactive parts of this page (fetching, operator unlock, connect/
 * disconnect) live in the client component `SettingsClient`, which never
 * recomputes this banner itself — it only ever renders the prop it's given.
 */
export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<{ gmail?: string | string[]; reason?: string | string[] }>;
}) {
  const params = await searchParams;
  const initialBanner = deriveGmailBanner(params);
  return <SettingsClient initialBanner={initialBanner} />;
}
