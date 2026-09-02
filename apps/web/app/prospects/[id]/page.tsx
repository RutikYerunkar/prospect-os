"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, NetworkError, approveProspect, getProspect, rejectProspect } from "@/lib/api";
import { formatConfidence, formatScore, formatStatus } from "@/lib/format";
import type { ApprovalState, EvidenceItem, ProspectAggregate, ProspectStatus } from "@/lib/types";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import { EvidenceCard } from "@/components/EvidenceCard";
import { SignalList } from "@/components/SignalList";
import { ContactPanel } from "@/components/ContactPanel";
import { OutreachViewer } from "@/components/OutreachViewer";
import { ReviewPanel } from "@/components/ReviewPanel";
import { TraceTable } from "@/components/TraceTable";

const STATUS_TONE: Record<string, BadgeTone> = {
  PENDING: "neutral",
  RUNNING: "sky",
  PASS: "emerald",
  NEEDS_REVIEW: "amber",
  REJECTED: "rose",
  DUPLICATE: "neutral",
  FAILED: "rose",
  TIMED_OUT: "rose",
};

const VERIFICATION_TONE: Record<string, BadgeTone> = {
  VERIFIED: "emerald",
  PERSONA_ONLY: "amber",
  UNAVAILABLE: "neutral",
};

const APPROVAL_TONE: Record<ApprovalState, BadgeTone> = {
  PENDING: "neutral",
  APPROVED: "emerald",
  REJECTED: "rose",
};

const DECIDABLE_STATUSES: ProspectStatus[] = ["PASS", "NEEDS_REVIEW", "REJECTED"];

function ProspectHeader({ prospect }: { prospect: ProspectAggregate }) {
  const displayName = (prospect.company.display_name as string | undefined) ?? "Unknown company";
  const domain = (prospect.company.canonical_domain as string | undefined) ?? "";

  return (
    <div className="flex flex-col gap-4 border-b border-zinc-800 bg-zinc-950 px-6 py-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link
          href={`/runs/${prospect.run_id}`}
          className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400"
        >
          ← Back to run
        </Link>
        <span className="font-mono text-xs text-zinc-600">{prospect.id}</span>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold text-zinc-100">{displayName}</h1>
        {domain && <span className="font-mono text-sm text-zinc-500">{domain}</span>}
        <Badge tone={STATUS_TONE[prospect.status] ?? "neutral"} className="text-xs">
          {formatStatus(prospect.status)}
        </Badge>
      </div>

      {prospect.status === "DUPLICATE" && prospect.duplicate_of && (
        <p className="text-sm text-zinc-500">
          Caught on a normalized dedupe key — earlier match:{" "}
          <Link href={`/prospects/${prospect.duplicate_of}`} className="font-mono text-indigo-400 hover:text-indigo-300">
            {prospect.duplicate_of}
          </Link>
        </p>
      )}

      {prospect.error && (
        <p className="max-w-2xl text-sm text-rose-400">
          <span className="text-zinc-600">Pipeline error — </span>
          {prospect.error}
        </p>
      )}

      <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] uppercase tracking-wide text-zinc-500">ICP score</span>
          <span className="font-mono text-lg tabular-nums leading-none text-zinc-100">
            {formatScore(prospect.score?.overall)}
          </span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] uppercase tracking-wide text-zinc-500">Confidence</span>
          <span className="font-mono text-lg tabular-nums leading-none text-zinc-100">
            {formatConfidence(prospect.score?.confidence)}
          </span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] uppercase tracking-wide text-zinc-500">Contact</span>
          <Badge tone={prospect.contact ? VERIFICATION_TONE[prospect.contact.verification] ?? "neutral" : "neutral"}>
            {prospect.contact ? prospect.contact.verification : "not reached"}
          </Badge>
        </div>
      </div>
    </div>
  );
}

function ApprovalBar({
  prospect,
  onDecide,
}: {
  prospect: ProspectAggregate;
  onDecide: (updated: ProspectAggregate) => void;
}) {
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const decidable = DECIDABLE_STATUSES.includes(prospect.status);
  const decided = prospect.approval.state !== "PENDING";

  async function handleApprove() {
    setPending("approve");
    setError(null);
    try {
      onDecide(await approveProspect(prospect.id));
    } catch (err) {
      setError(err instanceof ApiError ? (err.detail ?? err.message) : err instanceof NetworkError ? err.message : "approve failed");
    } finally {
      setPending(null);
    }
  }

  async function handleReject() {
    setPending("reject");
    setError(null);
    try {
      onDecide(await rejectProspect(prospect.id, reason.trim()));
    } catch (err) {
      setError(err instanceof ApiError ? (err.detail ?? err.message) : err instanceof NetworkError ? err.message : "reject failed");
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-zinc-500">Decision</span>
        <Badge tone={APPROVAL_TONE[prospect.approval.state]}>{prospect.approval.state}</Badge>
        {decided && (
          <span className="text-xs text-zinc-500">
            by {prospect.approval.actor ?? "—"}
            {prospect.approval.decided_at && ` · ${new Date(prospect.approval.decided_at).toLocaleString()}`}
          </span>
        )}
      </div>

      {decided && prospect.approval.reason && (
        <p className="text-sm text-zinc-400">
          <span className="text-zinc-600">Reason — </span>
          {prospect.approval.reason}
        </p>
      )}

      {!decidable ? (
        <p className="text-sm text-zinc-500">
          This prospect never reached a review verdict, so there is nothing for a human to decide yet.
        </p>
      ) : decided ? (
        <p className="text-sm text-zinc-500">
          Decision recorded. This is an audit-trail entry — the pipeline&apos;s own {formatStatus(prospect.status)}{" "}
          verdict is unchanged.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-zinc-500">
            Approve or reject transitions state via the existing audit trail only. Nothing is sent externally.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" onClick={handleApprove} disabled={pending !== null}>
              {pending === "approve" ? "Approving…" : "Approve"}
            </Button>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Reason for rejecting…"
              className="min-w-[220px] flex-1 rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400"
            />
            <Button
              variant="secondary"
              onClick={handleReject}
              disabled={pending !== null || reason.trim().length === 0}
            >
              {pending === "reject" ? "Rejecting…" : "Reject"}
            </Button>
          </div>
          {error && <p className="text-xs text-rose-400">{error}</p>}
        </div>
      )}
    </div>
  );
}

export default function ProspectDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [prospect, setProspect] = useState<ProspectAggregate | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadErrorUnreachable, setLoadErrorUnreachable] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getProspect(id)
      .then((data) => {
        if (cancelled) return;
        setProspect(data);
        setLoadError(null);
        setLoadErrorUnreachable(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadErrorUnreachable(err instanceof NetworkError);
        setLoadError(err instanceof ApiError ? (err.detail ?? err.message) : "failed to load prospect");
      });
    return () => {
      cancelled = true;
    };
  }, [id, reloadNonce]);

  if (loadError) {
    return (
      <main className="flex flex-1 items-center justify-center p-8">
        <div className="max-w-md text-center">
          <p className="text-sm text-rose-400">
            {loadErrorUnreachable ? (
              "Can't reach the API."
            ) : (
              <>Prospect <span className="font-mono">{id}</span> could not be loaded.</>
            )}
          </p>
          <p className="mt-2 text-xs text-zinc-500">
            {loadErrorUnreachable ? "Make sure the API process is running and reachable, then try again." : loadError}
          </p>
          <div className="mt-4 flex items-center justify-center gap-3">
            <Button variant="secondary" onClick={() => setReloadNonce((n) => n + 1)}>
              Retry
            </Button>
            <Link href="/plays/new" className="text-sm text-indigo-400 hover:text-indigo-300">
              ← Start a new play
            </Link>
          </div>
        </div>
      </main>
    );
  }

  if (!prospect) {
    return (
      <main className="flex flex-1 items-center justify-center p-8">
        <p className="text-sm text-zinc-500">Loading prospect…</p>
      </main>
    );
  }

  const evidenceById: Record<string, EvidenceItem> = Object.fromEntries(
    prospect.evidence.map((e) => [e.id, e]),
  );

  return (
    <main className="flex flex-1 flex-col">
      <ProspectHeader prospect={prospect} />

      <div className="flex-1 space-y-6 p-6">
        <Panel title="ICP Score Breakdown">
          <ScoreBreakdown score={prospect.score} />
        </Panel>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <Panel title="Signals">
            <SignalList signals={prospect.signals} evidenceById={evidenceById} />
          </Panel>
          <Panel title="Evidence" bodyClassName="flex flex-col gap-2.5 p-3">
            {prospect.evidence.length === 0 ? (
              <p className="p-1 text-sm text-zinc-500">No evidence recorded for this prospect.</p>
            ) : (
              prospect.evidence.map((e) => <EvidenceCard key={e.id} evidence={e} />)
            )}
          </Panel>
        </div>

        <Panel title="Contact / Buyer">
          <ContactPanel contact={prospect.contact} evidenceById={evidenceById} />
        </Panel>

        <Panel title="Outreach">
          <OutreachViewer drafts={prospect.drafts} evidenceById={evidenceById} />
        </Panel>

        <Panel title="Review & Guardrails">
          <ReviewPanel review={prospect.review} />
        </Panel>

        <Panel title="Approval">
          <ApprovalBar prospect={prospect} onDecide={setProspect} />
        </Panel>

        <Panel title="Execution Trace">
          <TraceTable trace={prospect.trace} />
        </Panel>
      </div>
    </main>
  );
}
