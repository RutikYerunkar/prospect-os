import type { FormEvent } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import type { GmailAvailability } from "@/lib/types";

export type GmailBanner = { kind: "connected" } | { kind: "error"; reason: string } | null;

/**
 * V2-G — pure/presentational: `/settings/page.tsx` owns all state and
 * fetching; this component only renders the four states the frozen plan
 * names (non-operator / operator+not-configured / operator+not-connected /
 * operator+connected) plus the unreachable-API and result-banner cases.
 * Kept presentational so it can be unit-tested with `renderToStaticMarkup`
 * the same way `ContactPanel`/`OutreachViewer` already are — no fetch, no
 * `useEffect`, no hidden state.
 *
 * NEVER rendered here, under any prop combination: a refresh token, client
 * secret, encryption key, PKCE verifier, or binding tag — this component
 * only ever receives `GmailAvailability`, which structurally cannot carry
 * any of those (see `groundwork/api/schemas.py::GmailAvailability`).
 */
export function GmailSettingsPanel({
  isOperator,
  operatorLoginConfigured,
  settingsUnreachable,
  gmail,
  banner,
  passphrase,
  onPassphraseChange,
  onUnlock,
  unlocking,
  unlockError,
  onConnect,
  connecting,
  connectError,
  onDisconnect,
  disconnecting,
  onLockAgain,
}: {
  isOperator: boolean;
  operatorLoginConfigured: boolean;
  settingsUnreachable: boolean;
  gmail: GmailAvailability | null;
  banner: GmailBanner;
  passphrase: string;
  onPassphraseChange: (value: string) => void;
  onUnlock: (e: FormEvent) => void;
  unlocking: boolean;
  unlockError: string | null;
  onConnect: () => void;
  connecting: boolean;
  connectError: string | null;
  onDisconnect: () => void;
  disconnecting: boolean;
  onLockAgain: () => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      {banner?.kind === "connected" && (
        <div className="rounded-md border border-emerald-700/40 bg-emerald-400/5 px-3 py-2 text-sm text-emerald-300">
          Gmail connected.
        </div>
      )}
      {banner?.kind === "error" && (
        <div
          role="alert"
          className="rounded-md border border-rose-700/40 bg-rose-400/5 px-3 py-2 text-sm text-rose-300"
        >
          Gmail connection failed ({banner.reason}).
        </div>
      )}

      <Panel title="Sending identity — Gmail">
        <div className="flex flex-col gap-3 p-4">
          {settingsUnreachable && (
            <p className="text-xs text-rose-400">Could not reach the API — is it running?</p>
          )}

          {/* State 1: non-operator */}
          {!settingsUnreachable && !isOperator && (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-zinc-400">Sign in as operator to manage the sending identity.</p>
              {operatorLoginConfigured && (
                <form
                  onSubmit={onUnlock}
                  className="flex flex-col gap-2 rounded-md border border-zinc-700 bg-zinc-900 p-3"
                >
                  <div className="flex gap-2">
                    <input
                      type="password"
                      value={passphrase}
                      onChange={(e) => onPassphraseChange(e.target.value)}
                      placeholder="Operator passphrase"
                      autoComplete="current-password"
                      className="flex-1 rounded-md border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-sm text-zinc-100 outline-none focus:border-indigo-400"
                    />
                    <Button type="submit" variant="secondary" disabled={unlocking || !passphrase}>
                      {unlocking ? "Unlocking…" : "Unlock"}
                    </Button>
                  </div>
                  {unlockError && (
                    <p role="alert" className="text-xs text-rose-400">
                      {unlockError}
                    </p>
                  )}
                </form>
              )}
            </div>
          )}

          {/* State 2: operator, OAuth not configured */}
          {!settingsUnreachable && isOperator && !gmail?.configured && (
            <p className="text-sm text-zinc-400">Gmail OAuth is not configured on this deployment.</p>
          )}

          {/* State 3: operator, configured, not connected */}
          {!settingsUnreachable && isOperator && gmail?.configured && !gmail.connected && (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-zinc-400">No Gmail account is connected.</p>
              <div>
                <Button onClick={onConnect} disabled={connecting}>
                  {connecting ? "Connecting…" : "Connect Gmail"}
                </Button>
              </div>
              {connectError && (
                <p role="alert" className="text-xs text-rose-400">
                  {connectError}
                </p>
              )}
            </div>
          )}

          {/* State 4: operator, connected */}
          {!settingsUnreachable && isOperator && gmail?.configured && gmail.connected && (
            <div className="flex flex-col gap-2 text-sm">
              <div className="flex items-center gap-2">
                <Badge tone="emerald">Connected</Badge>
                <span className="font-mono text-zinc-200">{gmail.google_account_email}</span>
              </div>
              <div className="flex flex-wrap gap-1">
                {gmail.scopes.map((scope) => (
                  <Badge key={scope} tone="neutral" mono>
                    {scope}
                  </Badge>
                ))}
              </div>
              {gmail.connected_at && (
                <p className="text-xs text-zinc-500">Connected {new Date(gmail.connected_at).toLocaleString()}</p>
              )}
              <div>
                <Button variant="secondary" onClick={onDisconnect} disabled={disconnecting}>
                  {disconnecting ? "Disconnecting…" : "Disconnect"}
                </Button>
              </div>
              {connectError && (
                <p role="alert" className="text-xs text-rose-400">
                  {connectError}
                </p>
              )}
            </div>
          )}

          {!settingsUnreachable && isOperator && (
            <button
              type="button"
              className="self-start text-xs text-zinc-500 underline hover:text-zinc-300"
              onClick={onLockAgain}
            >
              Lock operator session
            </button>
          )}
        </div>
      </Panel>
    </div>
  );
}
