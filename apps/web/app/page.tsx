"use client";

import { useEffect, useState } from "react";
import { apiGet, ApiError, type HealthResponse } from "@/lib/api";

type LoadState =
  | { status: "loading" }
  | { status: "ok"; health: HealthResponse }
  | { status: "error"; message: string };

export default function HomePage() {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    apiGet<HealthResponse>("/api/health")
      .then((health) => {
        if (!cancelled) setState({ status: "ok", health });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof ApiError ? err.message : "Could not reach the API";
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="flex flex-1 items-center justify-center p-8">
      <div className="w-full max-w-md rounded-lg border border-neutral-800 bg-neutral-950 p-6 text-neutral-100">
        <h1 className="text-lg font-semibold">Groundwork</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Agentic GTM research and qualification workspace
        </p>

        <div className="mt-6 rounded-md border border-neutral-800 bg-neutral-900 p-4 font-mono text-sm">
          {state.status === "loading" && <p className="text-neutral-400">Checking API health…</p>}
          {state.status === "error" && (
            <p className="text-rose-400">API unreachable: {state.message}</p>
          )}
          {state.status === "ok" && (
            <dl className="space-y-1">
              <div className="flex justify-between">
                <dt className="text-neutral-500">status</dt>
                <dd className="text-emerald-400">{state.health.status}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-neutral-500">mode</dt>
                <dd>{state.health.mode}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-neutral-500">version</dt>
                <dd>{state.health.version}</dd>
              </div>
            </dl>
          )}
        </div>
      </div>
    </main>
  );
}
