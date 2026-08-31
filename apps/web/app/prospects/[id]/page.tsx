"use client";

import { use } from "react";
import Link from "next/link";
import { Panel } from "@/components/ui/Panel";

/**
 * Minimal placeholder — Checkpoint E owns the full aggregate view (score
 * breakdown, evidence, signals, buyer, outreach, review, trace table). This
 * exists only so a prospect row's navigation target isn't a broken link.
 */
export default function ProspectDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 p-6">
      <Panel title="Prospect" className="text-sm">
        <div className="space-y-3 p-6">
          <p className="text-zinc-300">
            Prospect <span className="font-mono text-zinc-100">{id}</span>
          </p>
          <p className="text-zinc-500">
            Score breakdown, evidence, signals, buyer, outreach, and review land in Checkpoint E.
          </p>
          <Link
            href="/plays/new"
            className="inline-block text-sm text-indigo-400 hover:text-indigo-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400"
          >
            ← Start a new play
          </Link>
        </div>
      </Panel>
    </main>
  );
}
