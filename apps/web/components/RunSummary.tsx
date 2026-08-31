import { useEffect, useState } from "react";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Progress } from "@/components/ui/Progress";
import { Stat } from "@/components/ui/Stat";
import { formatElapsedSince, formatStatus } from "@/lib/format";
import { MAX_CONCURRENT_PROSPECTS } from "@/lib/constants";
import type { ProspectSummary, RunResponse } from "@/lib/types";
import type { ConnectionState } from "@/lib/useRunStream";

const RUN_STATUS_TONE: Record<string, BadgeTone> = {
  RUNNING: "sky",
  COMPLETED: "emerald",
  PARTIAL: "amber",
  INTERRUPTED: "rose",
};

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  connecting: "connecting…",
  live: "live",
  reconnecting: "reconnecting…",
  closed: "stream closed",
  error: "connection error",
};

const CONNECTION_TONE: Record<ConnectionState, BadgeTone> = {
  connecting: "neutral",
  live: "emerald",
  reconnecting: "amber",
  closed: "neutral",
  error: "rose",
};

function TERMINAL(status: ProspectSummary["status"]): boolean {
  return status !== "RUNNING" && status !== "PENDING";
}

function isActivelyExecuting(p: ProspectSummary): boolean {
  return !TERMINAL(p.status) && p.stage !== "DISCOVERED";
}

export function RunSummary({
  run,
  objective,
  prospects,
  connection,
}: {
  run: RunResponse;
  objective: string | null;
  prospects: ProspectSummary[] | null;
  connection: ConnectionState;
}) {
  const [, setTick] = useState(0);

  useEffect(() => {
    if (run.status !== "RUNNING") return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [run.status]);

  const list = prospects ?? [];
  const discovered = list.length;
  const active = list.filter(isActivelyExecuting).length;
  const completed = list.filter((p) => TERMINAL(p.status)).length;
  const pass = list.filter((p) => p.status === "PASS").length;
  const needsReview = list.filter((p) => p.status === "NEEDS_REVIEW").length;
  const rejected = list.filter((p) => p.status === "REJECTED").length;
  const duplicate = list.filter((p) => p.status === "DUPLICATE").length;
  const failed = list.filter((p) => p.status === "FAILED" || p.status === "TIMED_OUT").length;

  return (
    <div className="flex flex-col gap-4 border-b border-zinc-800 bg-zinc-950 px-6 py-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <h1 className="text-base font-semibold text-zinc-100">Run</h1>
          <span className="font-mono text-sm text-zinc-500">{run.id}</span>
          <Badge tone={RUN_STATUS_TONE[run.status] ?? "neutral"}>{formatStatus(run.status)}</Badge>
          <Badge tone="indigo">{run.mode.toUpperCase()}</Badge>
          <Badge tone={CONNECTION_TONE[connection]}>{CONNECTION_LABEL[connection]}</Badge>
        </div>
        <div className="font-mono text-sm text-zinc-400">
          {formatElapsedSince(run.started_at, run.finished_at)}
        </div>
      </div>

      {objective && (
        <p className="max-w-3xl text-sm text-zinc-400">
          <span className="text-zinc-600">Objective — </span>
          {objective}
        </p>
      )}

      <Progress value={completed} max={Math.max(discovered, 1)} tone="indigo" className="max-w-md" />

      <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
        <Stat label="Discovered" value={discovered} />
        <Stat
          label={`Agents active`}
          value={`${active} / ${MAX_CONCURRENT_PROSPECTS}`}
          tone={active > 0 ? "sky" : undefined}
        />
        <Stat label="Completed" value={completed} />
        <Stat label="Pass" value={pass} tone="emerald" />
        <Stat label="Needs review" value={needsReview} tone="amber" />
        <Stat label="Rejected" value={rejected} tone="rose" />
        <Stat label="Duplicate" value={duplicate} />
        <Stat label="Failed" value={failed} tone="rose" />
      </div>
    </div>
  );
}
