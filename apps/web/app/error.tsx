"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";

/**
 * Checkpoint I1 Phase 9 — the app-wide error boundary. Next.js renders this
 * in place of a page whenever a render (not a fetch — those are handled
 * per-page via friendly `loadError` states) throws. Never shows the raw
 * error to the user beyond its message; a full stack trace only goes to the
 * console, for whoever's actually debugging this session.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const router = useRouter();

  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="flex flex-1 items-center justify-center p-8">
      <div className="max-w-md text-center">
        <p className="text-sm text-rose-400">Something went wrong.</p>
        <p className="mt-2 text-xs text-zinc-500">
          This page hit an unexpected error. Try again, or head back to New Play.
        </p>
        {error.digest && <p className="mt-2 font-mono text-xs text-zinc-600">ref: {error.digest}</p>}
        <div className="mt-4 flex justify-center gap-2">
          <Button variant="secondary" onClick={reset}>
            Try again
          </Button>
          <Button onClick={() => router.push("/plays/new")}>New Play</Button>
        </div>
      </div>
    </main>
  );
}
