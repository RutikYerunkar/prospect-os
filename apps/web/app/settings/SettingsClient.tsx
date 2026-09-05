"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  ApiError,
  NetworkError,
  connectGmail,
  disconnectGmail,
  getProviderSettings,
  loginOperator,
  logoutOperator,
} from "@/lib/api";
import type { ProviderSettingsResponse } from "@/lib/types";
import { GmailSettingsPanel, type GmailBanner } from "@/components/GmailSettingsPanel";

function friendlyErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail || fallback;
  if (err instanceof NetworkError) return err.message;
  return fallback;
}

/**
 * Removes `?gmail=...&reason=...` from the address bar once the banner it
 * describes has already been rendered — so a later refresh doesn't replay
 * it. Deliberately POST-hydration only (an effect, not a lazy initializer):
 * `initialBanner` below is what actually decides the first render's
 * markup, computed server-side by `page.tsx` from the same query string —
 * this effect only tidies the URL afterward and never feeds back into
 * `banner` state.
 */
function useClearGmailQueryParamsAfterMount(): void {
  useEffect(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("gmail") && !url.searchParams.has("reason")) return;
    url.searchParams.delete("gmail");
    url.searchParams.delete("reason");
    window.history.replaceState({}, "", url.toString());
  }, []);
}

export function SettingsClient({ initialBanner }: { initialBanner: GmailBanner }) {
  const [providerSettings, setProviderSettings] = useState<ProviderSettingsResponse | null>(null);
  const [settingsUnreachable, setSettingsUnreachable] = useState(false);
  // Initialized directly from the server-computed prop — identical value on
  // the server's render and the client's first render, so there is nothing
  // here for hydration to disagree about. Never re-derived from
  // `window.location`/`Date.now()`/`Math.random()`.
  const [banner] = useState<GmailBanner>(initialBanner);

  const [passphrase, setPassphrase] = useState("");
  const [unlocking, setUnlocking] = useState(false);
  const [unlockError, setUnlockError] = useState<string | null>(null);

  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  useClearGmailQueryParamsAfterMount();

  function loadProviderSettings() {
    getProviderSettings()
      .then((s) => {
        setProviderSettings(s);
        setSettingsUnreachable(false);
      })
      .catch(() => {
        setProviderSettings(null);
        setSettingsUnreachable(true);
      });
  }

  useEffect(() => {
    loadProviderSettings();
  }, []);

  async function handleUnlock(e: FormEvent) {
    e.preventDefault();
    setUnlockError(null);
    setUnlocking(true);
    try {
      await loginOperator({ passphrase });
      setPassphrase("");
      loadProviderSettings();
    } catch (err) {
      setUnlockError(friendlyErrorMessage(err, "Could not reach the API — is it running?"));
    } finally {
      setUnlocking(false);
    }
  }

  async function handleLockAgain() {
    try {
      await logoutOperator();
    } catch {
      // best-effort — the cookie may already be gone/expired
    }
    loadProviderSettings();
  }

  async function handleConnect() {
    setConnectError(null);
    setConnecting(true);
    try {
      const { authorization_url } = await connectGmail();
      window.location.assign(authorization_url);
    } catch (err) {
      setConnectError(friendlyErrorMessage(err, "Could not start the Gmail connection."));
      setConnecting(false);
    }
  }

  async function handleDisconnect() {
    setConnectError(null);
    setDisconnecting(true);
    try {
      await disconnectGmail();
      loadProviderSettings();
    } catch (err) {
      setConnectError(friendlyErrorMessage(err, "Could not disconnect Gmail."));
    } finally {
      setDisconnecting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 p-6">
      <h1 className="text-lg font-semibold text-zinc-100">Settings</h1>
      <GmailSettingsPanel
        isOperator={providerSettings?.live?.is_operator ?? false}
        operatorLoginConfigured={providerSettings?.live?.operator_login_configured ?? false}
        settingsUnreachable={settingsUnreachable}
        gmail={providerSettings?.gmail ?? null}
        banner={banner}
        passphrase={passphrase}
        onPassphraseChange={setPassphrase}
        onUnlock={handleUnlock}
        unlocking={unlocking}
        unlockError={unlockError}
        onConnect={handleConnect}
        connecting={connecting}
        connectError={connectError}
        onDisconnect={handleDisconnect}
        disconnecting={disconnecting}
        onLockAgain={handleLockAgain}
      />
    </div>
  );
}
