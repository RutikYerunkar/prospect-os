"use client";

/**
 * V2-H — the third run-page tab. Lists every prospect that has reached a
 * review verdict (the same set the prospect-detail Approval panel already
 * treats as decidable) and lazily loads each one's full aggregate (drafts,
 * contact channels, review) on expand, so `ActionApprovalPanel` can drive
 * the whole propose -> approve -> execute flow inline without navigating
 * away from the run board.
 */

import { useState } from "react";
import { ApiError, getProspect } from "@/lib/api";
import type { ProspectAggregate, ProspectStatus, ProspectSummary } from "@/lib/types";
import { formatStatus } from "@/lib/format";
import { ActionApprovalPanel } from "@/components/ActionApprovalPanel";
import { Badge, type BadgeTone } from "@/components/ui/Badge";

const STATUS_TONE: Record<string, BadgeTone> = { PASS: "emerald", NEEDS_REVIEW: "amber", REJECTED: "rose" };
const DECIDABLE_STATUSES: ProspectStatus[] = ["PASS", "NEEDS_REVIEW", "REJECTED"];

export function OutreachTab({ prospects }: { prospects: ProspectSummary[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [aggregates, setAggregates] = useState<Record<string, ProspectAggregate>>({});
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const candidates = prospects.filter((p) => DECIDABLE_STATUSES.includes(p.status));

  async function toggle(id: string) {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    setError(null);
    if (!aggregates[id]) {
      setLoadingId(id);
      try {
        const aggregate = await getProspect(id);
        setAggregates((prev) => ({ ...prev, [id]: aggregate }));
      } catch (err) {
        setError(err instanceof ApiError ? (err.detail ?? err.message) : "failed to load this prospect");
      } finally {
        setLoadingId(null);
      }
    }
  }

  if (candidates.length === 0) {
    return (
      <p className="p-4 text-sm text-zinc-500">
        No prospect in this run has reached a review verdict yet — outreach actions become available once a
        prospect finishes the pipeline.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2 p-3">
      {candidates.map((p) => (
        <div key={p.id} className="rounded border border-zinc-800">
          <button
            onClick={() => toggle(p.id)}
            className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm text-zinc-200 hover:bg-zinc-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400"
          >
            <span className="flex items-center gap-2">
              <span className="font-mono text-xs text-zinc-500">{expandedId === p.id ? "▾" : "▸"}</span>
              {p.company_name}
            </span>
            <Badge tone={STATUS_TONE[p.status] ?? "neutral"}>{formatStatus(p.status)}</Badge>
          </button>
          {expandedId === p.id && (
            <div className="border-t border-zinc-800">
              {loadingId === p.id ? (
                <p className="p-4 text-sm text-zinc-500">Loading…</p>
              ) : error ? (
                <p className="p-4 text-sm text-rose-400">{error}</p>
              ) : aggregates[p.id] ? (
                <ActionApprovalPanel prospect={aggregates[p.id]} />
              ) : null}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
