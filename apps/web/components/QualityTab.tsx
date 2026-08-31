import { useEffect, useRef, useState } from "react";
import { ApiError, getRunEvaluation } from "@/lib/api";
import { MetricGrid } from "@/components/MetricGrid";
import { GuardrailPanel } from "@/components/GuardrailPanel";
import { ModelUsagePanel } from "@/components/ModelUsagePanel";
import type { ProspectSummary, RunEvaluation, RunStatus } from "@/lib/types";

const POLL_MS = 2000;

/**
 * Backed only by GET /runs/{id}/evaluation (Checkpoint C) — every number is
 * a real computed aggregate over this run's own records, no metrics table,
 * nothing hardcoded. Polls lightly while the run is still going so the tab
 * stays live if opened mid-run; a single fetch is enough once terminal.
 */
export function QualityTab({
  runId,
  runStatus,
  prospects,
}: {
  runId: string;
  runStatus: RunStatus;
  prospects: ProspectSummary[] | null;
}) {
  const [evaluation, setEvaluation] = useState<RunEvaluation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const unmountedRef = useRef(false);

  useEffect(() => {
    unmountedRef.current = false;

    async function fetchOnce() {
      try {
        const data = await getRunEvaluation(runId);
        if (!unmountedRef.current) {
          setEvaluation(data);
          setError(null);
        }
      } catch (err) {
        if (!unmountedRef.current) {
          setError(err instanceof ApiError ? err.detail ?? err.message : "Could not load evaluation");
        }
      }
    }

    void fetchOnce();
    const isLive = runStatus === "RUNNING";
    const interval = isLive ? setInterval(() => void fetchOnce(), POLL_MS) : null;

    return () => {
      unmountedRef.current = true;
      if (interval) clearInterval(interval);
    };
  }, [runId, runStatus]);

  if (error) {
    return (
      <div className="p-6 text-center">
        <p className="text-sm text-rose-400">Quality metrics could not be loaded.</p>
        <p className="mt-2 font-mono text-xs text-zinc-500">{error}</p>
      </div>
    );
  }

  if (!evaluation) {
    return <p className="p-6 text-sm text-zinc-500">Loading quality metrics…</p>;
  }

  if (evaluation.volume.discovered === 0) {
    return (
      <p className="p-6 text-sm text-zinc-500">
        No prospects discovered yet — quality metrics appear once discovery completes and prospects
        start moving through the pipeline.
      </p>
    );
  }

  return (
    <div className="flex flex-col divide-y divide-zinc-800">
      <div className="px-4 pt-3">
        <p className="text-xs text-zinc-500">
          Computed on read from this run&apos;s own records — there is no metrics table to drift.
          {runStatus === "RUNNING" && " Refreshing every few seconds while the run is in progress."}
        </p>
      </div>
      <MetricGrid volume={evaluation.volume} quality={evaluation.quality} reliability={evaluation.reliability} />
      <ModelUsagePanel usage={evaluation.llm_usage} />
      <div>
        <h3 className="px-4 pt-4 text-xs font-medium uppercase tracking-wide text-zinc-500">
          Guardrails — all seven deterministic checks
        </h3>
        <GuardrailPanel guardrails={evaluation.guardrails} prospects={prospects} />
      </div>
    </div>
  );
}
