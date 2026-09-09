import { Badge } from "@/components/ui/Badge";
import type { ActionMetrics } from "@/lib/types";

function Cell({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "amber";
}) {
  return (
    <div title={hint} className="flex flex-col gap-1 rounded border border-zinc-800 bg-zinc-950/40 px-3 py-2.5">
      <span className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</span>
      <span className={`font-mono text-lg tabular-nums leading-none ${tone === "amber" ? "text-amber-400" : "text-zinc-100"}`}>
        {value}
      </span>
    </div>
  );
}

function ReasonBadges({ entries, tone }: { entries: [string, number][]; tone: "amber" | "rose" }) {
  if (entries.length === 0) {
    return <span className="text-xs text-zinc-500">none</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {/* Generic — any reason string the backend ever emits renders
          correctly here, including one this component has never seen
          before (no hardcoded reason vocabulary). */}
      {entries.map(([reason, count]) => (
        <Badge key={reason} tone={tone} mono>
          {reason} · {count}
        </Badge>
      ))}
    </div>
  );
}

/**
 * V2-J §2/§3 — Action governance. Backed only by `/evaluation`'s `actions`
 * block; never hardcodes a proposal/execution count or a blocked-reason
 * string. A run where no governed action has ever been proposed shows an
 * explicit, unambiguous empty state — not a wall of zeros that would read
 * as a failure.
 */
export function ActionGovernancePanel({ actions }: { actions: ActionMetrics }) {
  const totalProposals = Object.values(actions.proposals_by_verdict).reduce((a, b) => a + b, 0);

  if (totalProposals === 0) {
    return (
      <div className="px-4 py-3">
        <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Action governance</h3>
        <p className="mt-2 text-sm text-zinc-500">
          No governed action has been proposed for this run yet.
        </p>
      </div>
    );
  }

  const verdictEntries = Object.entries(actions.proposals_by_verdict);
  const blockedReasonEntries = Object.entries(actions.blocked_reasons);
  const executionBlockedEntries = Object.entries(actions.execution_blocked_reasons);
  const statusEntries = Object.entries(actions.executions_by_status);
  const originEntries = Object.entries(actions.executions_by_origin);
  const reconciliationEntries = Object.entries(actions.reconciliation_outcomes);

  return (
    <div className="flex flex-col gap-3 px-4 py-4">
      <h3
        title="Computed on read from action_proposals/action_executions/action_events/approvals."
        className="w-fit text-xs font-medium uppercase tracking-wide text-zinc-500"
      >
        Action governance
      </h3>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
        <Cell label="Proposals" value={String(totalProposals)} hint="Every action_proposals row for this run." />
        <Cell
          label="Execution-blocked attempts"
          value={String(actions.execution_blocked_attempts)}
          tone={actions.execution_blocked_attempts > 0 ? "amber" : undefined}
          hint="Every 409 blocked at execute time — the five pre-policy gates plus a fresh policy re-check, counted per attempt (a retried proposal counts more than once)."
        />
        <Cell label="Distinct proposals blocked" value={String(actions.execution_blocked_proposals)} hint="Distinct proposals that hit at least one execution-time block." />
        <Cell label="Content-hash mismatches" value={String(actions.content_hash_mismatch_count)} hint="Execution-time blocks specifically because the draft changed after approval." />
        <Cell label="Uncertain executions" value={String(actions.uncertain_count)} hint="Executions whose delivery could not be confirmed — never auto-resent." />
        <Cell
          label="Cross-run recipient blocks"
          value={String(actions.cross_run_recipient_blocks)}
          hint="Blocked attempts where the conflicting prior send belongs to a DIFFERENT run than this one (DEMO_SIMULATED never participates in either direction)."
        />
        <Cell label="Cross-run blocked proposals" value={String(actions.cross_run_recipient_blocked_proposals)} hint="Distinct proposals blocked by a cross-run recipient conflict." />
        <Cell
          label="Approval → execution p50"
          value={actions.approval_to_execution_latency_p50_ms === null ? "—" : `${Math.round(actions.approval_to_execution_latency_p50_ms)}ms`}
        />
        <Cell
          label="Approval → execution p95"
          value={actions.approval_to_execution_latency_p95_ms === null ? "—" : `${Math.round(actions.approval_to_execution_latency_p95_ms)}ms`}
        />
        <Cell
          label="Mean messages scanned/reconcile"
          value={actions.mean_messages_scanned_per_reconcile === null ? "—" : actions.mean_messages_scanned_per_reconcile.toFixed(1)}
          hint="Average Gmail messages inspected per bounded reconciliation attempt — zero-egress until this point in the flow."
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div className="flex flex-col gap-2">
          <h4 title="Deterministic action-policy verdict at proposal time." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Proposals by verdict
          </h4>
          <div className="flex flex-wrap gap-1.5">
            {verdictEntries.map(([verdict, count]) => (
              <Badge key={verdict} tone={verdict === "ELIGIBLE" ? "emerald" : "amber"} mono>
                {verdict} · {count}
              </Badge>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-2">
          <h4 title="Why a proposal was BLOCKED at creation time — kept strictly separate from execution-time blocks." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Proposal-time blocked reasons
          </h4>
          <ReasonBadges entries={blockedReasonEntries} tone="amber" />
        </div>
        <div className="flex flex-col gap-2">
          <h4 title="Why an execute attempt was refused — the five pre-policy gates (not approved, approval superseded, sender not connected, sender changed, content changed) plus a fresh policy re-check." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Execution-time blocked reasons
          </h4>
          <ReasonBadges entries={executionBlockedEntries} tone="rose" />
        </div>
        <div className="flex flex-col gap-2">
          <h4 title="Terminal and in-flight execution status distribution." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Executions by status
          </h4>
          {statusEntries.length === 0 ? (
            <span className="text-xs text-zinc-500">none executed yet</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {statusEntries.map(([status, count]) => (
                <Badge
                  key={status}
                  tone={status === "SUCCEEDED" ? "emerald" : status === "FAILED" ? "rose" : status === "UNCERTAIN" ? "amber" : "neutral"}
                  mono
                >
                  {status} · {count}
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <h4 title="DEMO_SIMULATED (zero-egress) vs LIVE_EXTERNAL (a real external side effect is possible) — origin is not proof of delivery." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Executions by origin
          </h4>
          {originEntries.length === 0 ? (
            <span className="text-xs text-zinc-500">none executed yet</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {originEntries.map(([origin, count]) => (
                <Badge key={origin} tone={origin === "LIVE_EXTERNAL" ? "sky" : "indigo"} mono>
                  {origin} · {count}
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <h4 title="Bounded reconciliation outcome vocabulary — NOT_FOUND_WITHIN_BOUNDS/AMBIGUOUS/HISTORY_EXPIRED/LOOKUP_FAILED never convert to FAILED." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Reconciliation outcomes
          </h4>
          {reconciliationEntries.length === 0 ? (
            <span className="text-xs text-zinc-500">no reconciliation attempts</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {reconciliationEntries.map(([outcome, count]) => (
                <Badge key={outcome} tone={outcome === "FOUND" ? "emerald" : outcome === "ABANDONED" ? "rose" : "neutral"} mono>
                  {outcome} · {count}
                </Badge>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
