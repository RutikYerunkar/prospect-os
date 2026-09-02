"use client";

import { use, useEffect, useState } from "react";
import { apiGet, getProviderSettings } from "@/lib/api";
import type { PlayResponse } from "@/lib/types";
import { useRunStream } from "@/lib/useRunStream";
import { Panel } from "@/components/ui/Panel";
import { Button } from "@/components/ui/Button";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { RunSummary } from "@/components/RunSummary";
import { RunBoard } from "@/components/RunBoard";
import { ActivityStream } from "@/components/ActivityStream";
import { QualityTab } from "@/components/QualityTab";

export default function RunDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { run, prospects, events, retrying, connection, loadError, loadErrorUnreachable, retry } = useRunStream(id);
  const [tab, setTab] = useState<"board" | "quality">("board");
  const [play, setPlay] = useState<PlayResponse | null>(null);
  // Checkpoint I1 Phase 9: sourced from the API rather than a duplicated
  // frontend constant. `null` while loading — RunSummary treats that as
  // "unknown" (falls back to the same 3 the API has always defaulted to)
  // rather than blocking the whole page on this one field.
  const [maxConcurrentProspects, setMaxConcurrentProspects] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    getProviderSettings()
      .then((s) => {
        if (!cancelled) setMaxConcurrentProspects(s.max_concurrent_prospects);
      })
      .catch(() => {
        // nice-to-have context — a failure here shouldn't block the board
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
            {loadErrorUnreachable
              ? "Can't reach the API."
              : <>Run <span className="font-mono">{id}</span> could not be loaded.</>}
          </p>
          <p className="mt-2 text-xs text-zinc-500">
            {loadErrorUnreachable
              ? "Make sure the API process is running and reachable, then try again."
              : loadError}
          </p>
          <Button variant="secondary" className="mt-4" onClick={retry}>
            Retry
          </Button>
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
      <RunSummary
        run={run}
        objective={play?.objective_text ?? null}
        prospects={prospects}
        connection={connection}
        maxConcurrentProspects={maxConcurrentProspects ?? 3}
      />

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
              <QualityTab runId={run.id} runStatus={run.status} prospects={prospects} />
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
