"use client";

/**
 * V2-I-b Phase 10 — the immutable action audit trail, plus operator-only
 * reconcile/recover controls for a LIVE_EXTERNAL execution.
 *
 * Exact required copy (never paraphrased, never "delivered" for SUCCEEDED):
 * - SUCCEEDED  -> "Gmail accepted this message"
 * - UNCERTAIN  -> "acceptance not established — Groundwork will not resend"
 * - ABANDONED  -> "stopped checking; this is not evidence the message was not sent"
 *
 * Never renders a raw Gmail provider payload, an OAuth token, or raw MIME —
 * only the already-derived, safe fields the audit API returns. There is no
 * resend button/control anywhere in this component, by design (D7/D10).
 */

import { useEffect, useState } from "react";
import { ApiError, getActionAudit, getProviderSettings, reconcileExecution, recoverExecution } from "@/lib/api";
import type { ActionAudit } from "@/lib/types";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { reasonCopy } from "@/components/ActionApprovalPanel";

const OUTCOME_COPY: Record<string, string> = {
  SUCCEEDED: "Gmail accepted this message",
  UNCERTAIN: "acceptance not established — Groundwork will not resend",
  ABANDONED: "stopped checking; this is not evidence the message was not sent",
  FAILED: "provably not dispatched, or definitively rejected before delivery could be attempted",
};

const STATUS_TONE: Record<string, BadgeTone> = {
  CLAIMED: "sky",
  IN_FLIGHT: "sky",
  SUCCEEDED: "emerald",
  FAILED: "rose",
  UNCERTAIN: "amber",
  ABANDONED: "rose",
};

export function outcomeCopy(status: string): string | null {
  return OUTCOME_COPY[status] ?? null;
}

export function ActionAuditPanel({ proposalId }: { proposalId: string }) {
  const [audit, setAudit] = useState<ActionAudit | null>(null);
  const [isOperator, setIsOperator] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<"reconcile" | "recover" | null>(null);

  async function refresh() {
    try {
      const [a, settings] = await Promise.all([getActionAudit(proposalId), getProviderSettings()]);
      setAudit(a);
      setIsOperator(settings.live.is_operator);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? (err.detail ?? err.message) : "failed to load the audit trail");
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [a, settings] = await Promise.all([getActionAudit(proposalId), getProviderSettings()]);
        if (cancelled) return;
        setAudit(a);
        setIsOperator(settings.live.is_operator);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? (err.detail ?? err.message) : "failed to load the audit trail");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [proposalId]);

  async function handleReconcile() {
    if (!audit?.proposal.execution) return;
    setPending("reconcile");
    setError(null);
    try {
      await reconcileExecution(audit.proposal.execution.id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? (err.detail ?? err.message) : "reconcile failed");
    } finally {
      setPending(null);
    }
  }

  async function handleRecover() {
    if (!audit?.proposal.execution) return;
    setPending("recover");
    setError(null);
    try {
      await recoverExecution(audit.proposal.execution.id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? (err.detail ?? err.message) : "recover failed");
    } finally {
      setPending(null);
    }
  }

  if (error && !audit) {
    return <p className="text-xs text-rose-400">{error}</p>;
  }
  if (!audit) {
    return <p className="text-xs text-zinc-500">Loading audit trail…</p>;
  }

  const { proposal, events, send_calls: sendCalls } = audit;
  const execution = proposal.execution;
  const isLive = execution?.origin === "LIVE_EXTERNAL";
  const copy = execution ? outcomeCopy(execution.status) : null;

  return (
    <div className="flex flex-col gap-3 rounded border border-zinc-800 bg-zinc-950/60 p-3 text-xs">
      <p className="text-[10px] uppercase tracking-wide text-zinc-500">Audit trail</p>

      {proposal.blocked_reasons.length > 0 && (
        <div className="rounded border border-rose-900 bg-rose-950/30 p-2 text-rose-300">
          <p className="font-medium">Blocked — no override available.</p>
          <ul className="mt-1 list-disc pl-4">
            {proposal.blocked_reasons.map((reason) => (
              <li key={reason}>{reasonCopy(reason)}</li>
            ))}
          </ul>
        </div>
      )}

      {proposal.approval && (
        <p className="text-zinc-400">
          Approval: <span className="text-zinc-200">{proposal.approval.state}</span>
          {proposal.approval.actor && <> by {proposal.approval.actor}</>}
          {proposal.approval.decided_at && <> at {proposal.approval.decided_at}</>}
          {proposal.approval.reason && <> — {proposal.approval.reason}</>}
        </p>
      )}

      {execution ? (
        <div className="flex flex-col gap-1.5 border-t border-zinc-800 pt-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[execution.status] ?? "neutral"}>{execution.status}</Badge>
            <span className="text-zinc-500">origin: {execution.origin}</span>
            {execution.outcome_class && <span className="text-zinc-500">outcome: {execution.outcome_class}</span>}
          </div>
          {copy && <p className="text-zinc-300">{copy}</p>}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-zinc-500">
            <dt>Claimed</dt>
            <dd className="text-zinc-300">{execution.claimed_at ?? "—"}</dd>
            <dt>Dispatched</dt>
            <dd className="text-zinc-300">{execution.dispatched_at ?? "—"}</dd>
            <dt>Settled</dt>
            <dd className="text-zinc-300">{execution.settled_at ?? "—"}</dd>
            {isLive && (
              <>
                <dt>Reconcile attempts</dt>
                <dd className="text-zinc-300">{execution.reconcile_attempts}</dd>
                <dt>Messages scanned</dt>
                <dd className="text-zinc-300">{execution.messages_scanned}</dd>
                <dt>Last reconciled</dt>
                <dd className="text-zinc-300">{execution.reconciled_at ?? "—"}</dd>
              </>
            )}
          </dl>

          {isLive && isOperator && execution.status === "UNCERTAIN" && (
            <div className="mt-1 flex items-center gap-2">
              <Button variant="secondary" onClick={handleReconcile} disabled={pending !== null}>
                {pending === "reconcile" ? "Reconciling…" : "Reconcile"}
              </Button>
              <Button variant="secondary" onClick={handleRecover} disabled={pending !== null}>
                {pending === "recover" ? "Recovering…" : "Recover (stale claim)"}
              </Button>
            </div>
          )}
          {isLive && isOperator && (execution.status === "CLAIMED" || execution.status === "IN_FLIGHT") && (
            <div className="mt-1">
              <Button variant="secondary" onClick={handleRecover} disabled={pending !== null}>
                {pending === "recover" ? "Recovering…" : "Recover (stale claim)"}
              </Button>
            </div>
          )}
        </div>
      ) : (
        <p className="text-zinc-500">No execution yet.</p>
      )}

      {error && <p className="text-rose-400">{error}</p>}

      {events.length > 0 && (
        <div className="border-t border-zinc-800 pt-2">
          <p className="text-[10px] uppercase tracking-wide text-zinc-500">Events (immutable)</p>
          <ul className="mt-1 flex flex-col gap-0.5 text-zinc-400">
            {events.map((e) => (
              <li key={e.id}>
                <span className="text-zinc-300">{e.type}</span> — {e.actor} — {e.ts}
              </li>
            ))}
          </ul>
        </div>
      )}

      {sendCalls.length > 0 && (
        <div className="border-t border-zinc-800 pt-2">
          <p className="text-[10px] uppercase tracking-wide text-zinc-500">Provider calls</p>
          <ul className="mt-1 flex flex-col gap-0.5 text-zinc-400">
            {sendCalls.map((c) => (
              <li key={c.id}>
                <span className="text-zinc-300">{c.operation}</span> via {c.provider} — {c.status}
                {c.http_status != null && <> — HTTP {c.http_status}</>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
