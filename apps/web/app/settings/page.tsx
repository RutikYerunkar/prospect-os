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

// `GET /api/gmail/callback` redirects here with `?gmail=connected` or
// `?gmail=error&reason=...` — read once on mount, then scrub the query
// string so a refresh doesn't replay a stale banner.
function readAndClearGmailBanner(): GmailBanner {
  if (typeof window === "undefined") return null;
  const url = new URL(window.location.href);
  const gmail = url.searchParams.get("gmail");
  if (gmail !== "connected" && gmail !== "error") return null;

  const banner: GmailBanner = gmail === "connected" ? { kind: "connected" } : { kind: "error", reason: url.searchParams.get("reason") || "unknown" };
  url.searchParams.delete("gmail");
  url.searchParams.delete("reason");
  window.history.replaceState({}, "", url.toString());
  return banner;
}

export default function SettingsPage() {
  const [providerSettings, setProviderSettings] = useState<ProviderSettingsResponse | null>(null);
  const [settingsUnreachable, setSettingsUnreachable] = useState(false);
  // Lazy initializer (runs once, during the initial client render) rather
  // than an effect — reads/scrubs `?gmail=...` exactly once per mount, with
  // no setState call inside an effect body.
  const [banner] = useState<GmailBanner>(() => readAndClearGmailBanner());

  const [passphrase, setPassphrase] = useState("");
  const [unlocking, setUnlocking] = useState(false);
  const [unlockError, setUnlockError] = useState<string | null>(null);

  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

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
