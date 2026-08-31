"use client";

import { use, useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import type { PlayResponse } from "@/lib/types";
import { useRunStream } from "@/lib/useRunStream";
import { Panel } from "@/components/ui/Panel";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { RunSummary } from "@/components/RunSummary";
import { RunBoard } from "@/components/RunBoard";
import { ActivityStream } from "@/components/ActivityStream";

export default function RunDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { run, prospects, events, retrying, connection, loadError } = useRunStream(id);
  const [tab, setTab] = useState<"board" | "quality">("board");
  const [play, setPlay] = useState<PlayResponse | null>(null);

  useEffect(() => {
    if (!run?.play_id) return;
    let cancelled = false;
    apiGet<PlayResponse>(`/api/plays/${run.play_id}`)
      .then((p) => {
        if (!cancelled) setPlay(p);
      })
      .catch(() => {
        // objective is nice-to-have context in the header — a failure here
        // shouldn't block the run board from rendering
      });
    return () => {
      cancelled = true;
    };
  }, [run?.play_id]);

  if (loadError) {
    return (
      <main className="flex flex-1 items-center justify-center p-8">
        <div className="max-w-md text-center">
          <p className="text-sm text-rose-400">
            Run <span className="font-mono">{id}</span> could not be loaded.
          </p>
          <p className="mt-2 font-mono text-xs text-zinc-600">{loadError}</p>
        </div>
      </main>
    );
  }

  if (!run) {
    return (
      <main className="flex flex-1 items-center justify-center p-8">
        <p className="text-sm text-zinc-500">Loading run…</p>
      </main>
    );
  }

  return (
    <main className="flex flex-1 flex-col">
      <RunSummary run={run} objective={play?.objective_text ?? null} prospects={prospects} connection={connection} />

      <div className="flex-1 p-6">
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_360px]">
          <Panel bodyClassName="pb-0">
            <Tabs>
              <Tab active={tab === "board"} onClick={() => setTab("board")}>
                Board
              </Tab>
              <Tab active={tab === "quality"} onClick={() => setTab("quality")}>
                Quality
              </Tab>
            </Tabs>
            {tab === "board" ? (
              <RunBoard prospects={prospects} retrying={retrying} />
            ) : (
              <p className="p-6 text-sm text-zinc-500">
                Quality metrics available after completion — the full evaluation dashboard lands in
                Checkpoint F.
              </p>
            )}
          </Panel>

          <Panel title="Activity">
            <ActivityStream events={events} prospects={prospects} />
          </Panel>
        </div>
      </div>
    </main>
  );
}
