import type { ReactNode } from "react";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Progress } from "@/components/ui/Progress";
import { formatCount, formatDuration, formatPercent } from "@/lib/format";
import type { QualityMetrics, ReliabilityMetrics, VolumeMetrics } from "@/lib/types";

function MetricCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "emerald" | "amber" | "rose" | "sky";
}) {
  const toneClass =
    tone === "emerald"
      ? "text-emerald-400"
      : tone === "amber"
        ? "text-amber-400"
        : tone === "rose"
          ? "text-rose-400"
          : tone === "sky"
            ? "text-sky-400"
            : "text-zinc-100";
  return (
    <div title={hint} className="flex flex-col gap-1 rounded border border-zinc-800 bg-zinc-950/40 px-3 py-2.5">
      <span className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</span>
      <span className={`font-mono text-lg tabular-nums leading-none ${toneClass}`}>{value}</span>
    </div>
  );
}

function MetricGroup({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-3">
      <h3 title={hint} className="w-fit text-xs font-medium uppercase tracking-wide text-zinc-500">
        {title}
      </h3>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4">{children}</div>
    </div>
  );
}

function BreakdownRow({ counts, tones }: { counts: Record<string, number>; tones?: Record<string, BadgeTone> }) {
  const entries = Object.entries(counts);
  if (entries.length === 0) return <span className="text-xs text-zinc-500">no data yet</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([key, count]) => (
        <Badge key={key} tone={tones?.[key] ?? "neutral"} mono>
          {key} · {count}
        </Badge>
      ))}
    </div>
  );
}

/**
 * Volume / Grounding & quality / Reliability groups of the Quality tab —
 * every number read straight from `GET /runs/{id}/evaluation` (Checkpoint C,
 * computed on read, no metrics table). A `null` field renders "—", never a
 * fabricated placeholder — the same rule the backend already enforces.
 */
export function MetricGrid({
  volume,
  quality,
  reliability,
}: {
  volume: VolumeMetrics;
  quality: QualityMetrics;
  reliability: ReliabilityMetrics;
}) {
  const nonTerminal = (volume.by_status["RUNNING"] ?? 0) + (volume.by_status["PENDING"] ?? 0);
  const completed = volume.discovered - nonTerminal;
  const pass = volume.by_status["PASS"] ?? volume.qualified;
  const failedCount = volume.by_status["FAILED"] ?? 0;
  const timedOutCount = volume.by_status["TIMED_OUT"] ?? 0;

  const syntheticOnly =
    Object.keys(quality.provenance_mix).length > 0 &&
    Object.keys(quality.provenance_mix).every((k) => k === "DEMO_FIXTURE");

  const stepEntries = Object.entries(reliability.per_step_success_rate);
  const errorEntries = Object.entries(reliability.provider_error_counts);

  return (
    <div className="flex flex-col gap-6 p-4">
      <MetricGroup title="Volume" hint="Counted directly from this run's prospect rows and their engine-computed status.">
        <MetricCard label="Discovered" value={formatCount(volume.discovered)} hint="Prospects discovery returned for this run, before dedupe." />
        <MetricCard label="Completed" value={formatCount(completed)} hint="Prospects no longer PENDING/RUNNING — reached a terminal status." />
        <MetricCard label="Pass" value={formatCount(pass)} tone="emerald" hint="Status = PASS: cleared scoring, review, and the min-score/confidence gate." />
        <MetricCard label="Needs review" value={formatCount(volume.needs_review)} tone="amber" hint="Status = NEEDS_REVIEW: a soft guardrail check failed." />
        <MetricCard label="Rejected" value={formatCount(volume.rejected)} tone="rose" hint="Status = REJECTED: disqualified by scoring or a hard guardrail check." />
        <MetricCard label="Duplicate" value={formatCount(volume.duplicated)} hint="Status = DUPLICATE: caught by dedupe before the pipeline ran." />
        <MetricCard label="Failed" value={formatCount(volume.failed)} tone="rose" hint={`Status = FAILED or TIMED_OUT (${failedCount} failed, ${timedOutCount} timed out) — retries exhausted or the run watchdog expired.`} />
      </MetricGroup>

      <MetricGroup title="Grounding / quality" hint="Whether output was supportable, not just whether it existed.">
        <MetricCard label="Evidence coverage" value={formatPercent(quality.evidence_coverage)} hint="Share of non-duplicate prospects with ≥3 sourced evidence items." />
        <MetricCard label="Grounded claim rate" value={formatPercent(quality.grounded_claim_rate)} hint="Share of outreach claims whose cited evidence resolves and verifies via token-overlap." />
        <MetricCard label="Dimension support" value={formatPercent(quality.dimension_support_rate)} hint="Share of scored ICP dimensions across all prospects that had ≥1 supporting evidence item." />
        <MetricCard label="Unsupported claims" value={formatCount(quality.unsupported_claim_count)} tone={quality.unsupported_claim_count > 0 ? "amber" : undefined} hint="Absolute count of outreach claims with no verified evidence." />
        <MetricCard label="Mean ICP score" value={quality.mean_icp_score === null ? "—" : quality.mean_icp_score.toFixed(1)} hint="Average overall ICP score across every prospect a score was computed for." />
        <MetricCard label="Mean confidence" value={formatPercent(quality.mean_confidence)} hint="Average scoring confidence (evidence-supported dimensions ÷ total dimensions)." />
      </MetricGroup>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h4 title="VERIFIED / PERSONA_ONLY / UNAVAILABLE, one per prospect that reached contact resolution." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Contact verification breakdown
          </h4>
          <BreakdownRow
            counts={quality.contact_verification_breakdown}
            tones={{ VERIFIED: "emerald", PERSONA_ONLY: "amber", UNAVAILABLE: "neutral" }}
          />
        </div>
        <div className="flex flex-col gap-2">
          <h4 title="Origin of every evidence row: DEMO_FIXTURE (synthetic, no source URL), LIVE_FETCH (real source), LLM_INFERENCE (model-asserted, unsourced)." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Provenance mix
          </h4>
          <BreakdownRow
            counts={quality.provenance_mix}
            tones={{ DEMO_FIXTURE: "indigo", LIVE_FETCH: "emerald", LLM_INFERENCE: "amber" }}
          />
          {syntheticOnly && (
            <p className="text-xs text-zinc-500">
              100% synthetic — Demo Mode fixture evidence, no outbound fetch, no real source URLs.
            </p>
          )}
        </div>
      </div>

      <MetricGroup title="Reliability" hint="Step-level retry, timing, and error behavior over agent_tasks, this run's own attempt log.">
        <MetricCard label="Retries" value={formatCount(reliability.total_retries)} tone={reliability.total_retries > 0 ? "amber" : undefined} hint="Attempts recorded with status RETRY across every step and prospect." />
        <MetricCard label="p50 step duration" value={reliability.p50_step_duration_ms === null ? "—" : formatDuration(reliability.p50_step_duration_ms)} hint="Median duration of successful (OK) step attempts." />
        <MetricCard label="p95 step duration" value={reliability.p95_step_duration_ms === null ? "—" : formatDuration(reliability.p95_step_duration_ms)} hint="95th-percentile duration of successful (OK) step attempts." />
        <MetricCard label="Run wall time" value={reliability.run_wall_clock_ms === null ? "—" : formatDuration(reliability.run_wall_clock_ms)} hint="Time from run start to finish (or now, if still running)." />
      </MetricGroup>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h4 title="Share of prospects for which this step reached OK at least once (a retried-then-succeeded step still counts as a success)." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Per-step success rate
          </h4>
          {stepEntries.length === 0 ? (
            <span className="text-xs text-zinc-500">no attempts recorded yet</span>
          ) : (
            <div className="flex flex-col gap-1.5">
              {stepEntries.map(([step, rate]) => (
                <div key={step} className="flex items-center gap-2 text-xs">
                  <span className="w-24 shrink-0 text-zinc-400">{step}</span>
                  <Progress value={rate} max={1} tone={rate === 1 ? "emerald" : rate >= 0.5 ? "amber" : "rose"} className="max-w-[140px]" />
                  <span className="font-mono tabular-nums text-zinc-500">{formatPercent(rate)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <h4 title="Count of provider-raised errors by exception type, across every retry attempt." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Provider errors
          </h4>
          {errorEntries.length === 0 ? (
            <span className="text-xs text-zinc-500">none recorded</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {errorEntries.map(([type, count]) => (
                <Badge key={type} tone="amber" mono>
                  {type} · {count}
                </Badge>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
