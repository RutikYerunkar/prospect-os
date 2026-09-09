"use client";

/**
 * V2-H — Action proposal + human approval (Demo executor only).
 *
 * `draft -> explicit action proposal -> immutable hash/sender binding ->
 * human approval/rejection -> Demo execution -> immutable action audit
 * trail`. Shows, per draft: the proposed action/channel, the exact content
 * hash, the sending identity (where applicable), the policy/eligibility
 * state, the approval state, and the execution state. A BLOCKED proposal
 * shows its reasons and offers NO override control anywhere (D7) — there is
 * no button, query parameter, or hidden affordance that approves/executes a
 * blocked proposal; the server independently refuses it regardless.
 *
 * LinkedIn "Open" (V2-I-c): reuses `lib/linkedinSafety.ts::isSafeLinkedInHref`
 * against the LinkedIn contact-channel row's own `origin`/`discovery_state`/
 * `identifier` — the SAME defense-in-depth check `ContactPanel` already
 * applies (never a second URL parser). When it returns true, "Open" is a
 * real `<a target="_blank" rel="noreferrer noopener">` to the validated
 * identifier. When it returns false, "Open" reveals an inline fallback
 * panel instead — never an external navigation, and never a `demo://` (or
 * any other unsafe) value in an `<a href>` anywhere. A `DEMO_FIXTURE`
 * channel keeps the original "simulated profile, no network request"
 * copy; a `LIVE_PROVIDER` channel that still didn't pass the safety check
 * (not yet `RESOLVED`, or a malformed/non-LinkedIn URL) gets distinct,
 * provenance-honest copy — it must never claim "demo fixture" or "no
 * network request was made" about a real provider observation.
 */

import { useEffect, useState } from "react";
import {
  ApiError,
  approveAction,
  executeAction,
  listProspectActionProposals,
  proposeAction,
  rejectAction,
} from "@/lib/api";
import { isSafeLinkedInHref } from "@/lib/linkedinSafety";
import type { ActionProposal, ContactChannel, OutreachDraft, ProspectAggregate } from "@/lib/types";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ActionAuditPanel } from "@/components/ActionAuditPanel";

const POLICY_TONE: Record<string, BadgeTone> = { ELIGIBLE: "emerald", BLOCKED: "rose" };
const APPROVAL_TONE: Record<string, BadgeTone> = { PENDING: "neutral", APPROVED: "emerald", REJECTED: "rose" };
const EXECUTION_TONE: Record<string, BadgeTone> = {
  CLAIMED: "sky",
  IN_FLIGHT: "sky",
  SUCCEEDED: "emerald",
  FAILED: "rose",
  UNCERTAIN: "amber",
  ABANDONED: "rose",
};

const BLOCKED_REASON_COPY: Record<string, string> = {
  review_not_passed: "the review verdict is not PASS",
  prospect_not_actionable: "this prospect's status is not actionable",
  email_not_discovered: "no email address has been discovered for this contact",
  email_not_verified: "the discovered email is not VERIFIED — the only sendable state",
  contact_state_stale: "the contact state is stale and needs a fresh enrichment",
  draft_incomplete: "the draft is missing required content",
  recipient_identity_invalid: "the recipient address does not normalize to a valid identity",
  content_changed: "the content changed since this hash was computed",
  approval_superseded: "the approval no longer matches the current hash version",
  sender_not_connected: "no sending identity is currently connected",
  sender_changed: "the connected sending identity no longer matches this proposal",
  already_sent_to_recipient: "an initial email was already sent to this recipient",
  prior_send_uncertain: "a prior send to this recipient has an unresolved outcome",
  send_in_flight: "a send to this recipient is already in flight",
  send_provider_unavailable: "no send provider is available",
  send_allowance_exhausted: "the daily send allowance is exhausted",
  demo_action_cap_reached: "this run's demo action cap has been reached",
  linkedin_not_resolved: "no LinkedIn profile has been resolved for this contact",
  linkedin_identity_not_strong: "the LinkedIn identity match is not STRONG — a MISMATCH or weak match is never actionable",
  // V2-I-a — provider-neutral, no human-intent wording ("withdrew",
  // "consent", "claimed by its owner").
  recipient_suppressed: "this recipient was suppressed after a provider privacy/legal restriction signal — retained for audit, not sendable",
};

export function reasonCopy(reason: string): string {
  return BLOCKED_REASON_COPY[reason] ?? reason;
}

function LinkedInFallbackProfilePanel({
  prospect,
  isLiveProvider,
}: {
  prospect: ProspectAggregate;
  isLiveProvider: boolean;
}) {
  const name = prospect.contact?.full_name ?? "Unknown";
  const title = prospect.contact?.title ?? "Unknown title";
  const company = (prospect.company.display_name as string | undefined) ?? "Unknown company";
  return (
    <div className="mt-2 rounded border border-zinc-700 bg-zinc-900/80 p-3 text-xs text-zinc-300">
      <p className="text-[10px] uppercase tracking-wide text-zinc-500">
        {isLiveProvider ? "LinkedIn profile — not shown as a link" : "Simulated LinkedIn profile · demo fixture"}
      </p>
      <p className="mt-1 font-medium text-zinc-100">{name}</p>
      <p className="text-zinc-400">
        {title} at {company}
      </p>
      <p className="mt-1.5 text-[11px] text-zinc-600">
        {isLiveProvider
          ? "This LinkedIn identifier did not pass the safe-link check, so it is not offered as a clickable link — see the Contact panel for its current resolution state."
          : "No network request was made and no external page was opened — this is a local, simulated rendering only."}
      </p>
    </div>
  );
}

/**
 * Extracted from `ActionCard` (V2-I-c) so the anchor-vs-fallback decision
 * can be rendered and tested with explicit props — `renderToStaticMarkup`
 * never runs `useEffect`, so a test driving `ActionCard` itself can never
 * populate `proposal`/`openPanel` state to reach this branch at all.
 */
export function LinkedInOpenAction({
  prospect,
  linkedInChannel,
  openPanel,
  copied,
  onCopy,
  onToggleOpenPanel,
}: {
  prospect: ProspectAggregate;
  linkedInChannel: ContactChannel | null;
  openPanel: boolean;
  copied: boolean;
  onCopy: () => void;
  onToggleOpenPanel: () => void;
}) {
  const linkedInHref = isSafeLinkedInHref({
    channel: linkedInChannel?.channel,
    origin: linkedInChannel?.origin,
    discoveryState: linkedInChannel?.discovery_state,
    identifier: linkedInChannel?.identifier,
  })
    ? linkedInChannel?.identifier
    : null;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Button variant="secondary" onClick={onCopy}>
          {copied ? "Copied!" : "Copy message"}
        </Button>
        {linkedInHref ? (
          <a
            href={linkedInHref}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-xs font-medium text-indigo-400 hover:text-indigo-300"
          >
            Open profile ↗
          </a>
        ) : (
          <Button variant="secondary" onClick={onToggleOpenPanel}>
            {openPanel ? "Close profile" : "Open profile"}
          </Button>
        )}
      </div>
      {!linkedInHref && openPanel && (
        <LinkedInFallbackProfilePanel prospect={prospect} isLiveProvider={linkedInChannel?.origin === "LIVE_PROVIDER"} />
      )}
    </div>
  );
}

function ActionCard({ draft, prospect }: { draft: OutreachDraft; prospect: ProspectAggregate }) {
  const [proposal, setProposal] = useState<ActionProposal | null>(null);
  const [pending, setPending] = useState<"propose" | "approve" | "reject" | "execute" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [copied, setCopied] = useState(false);
  const [openPanel, setOpenPanel] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listProspectActionProposals(prospect.id)
      .then((rows) => {
        if (cancelled) return;
        const match = rows
          .filter((r) => r.draft_id === draft.id)
          .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
        if (match) setProposal(match);
      })
      .catch(() => {
        // nice-to-have prefill — a failure here just leaves the "Propose" button available
      });
    return () => {
      cancelled = true;
    };
  }, [prospect.id, draft.id]);

  async function handlePropose() {
    setPending("propose");
    setError(null);
    try {
      setProposal(await proposeAction(draft.id));
    } catch (err) {
      setError(err instanceof ApiError ? (err.detail ?? err.message) : "failed to propose this action");
    } finally {
      setPending(null);
    }
  }

  async function handleApprove() {
    if (!proposal) return;
    setPending("approve");
    setError(null);
    try {
      setProposal(await approveAction(proposal.id));
    } catch (err) {
      setError(err instanceof ApiError ? (err.detail ?? err.message) : "failed to approve this action");
    } finally {
      setPending(null);
    }
  }

  async function handleReject() {
    if (!proposal) return;
    setPending("reject");
    setError(null);
    try {
      setProposal(await rejectAction(proposal.id, rejectReason.trim() || "not a fit"));
    } catch (err) {
      setError(err instanceof ApiError ? (err.detail ?? err.message) : "failed to reject this action");
    } finally {
      setPending(null);
    }
  }

  async function handleExecute() {
    if (!proposal) return;
    setPending("execute");
    setError(null);
    try {
      setProposal(await executeAction(proposal.id));
    } catch (err) {
      setError(err instanceof ApiError ? (err.detail ?? err.message) : "failed to execute this action");
    } finally {
      setPending(null);
    }
  }

  async function handleCopy() {
    const text = draft.subject ? `${draft.subject}\n\n${draft.body}` : draft.body;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("clipboard write failed — your browser may have blocked it");
    }
  }

  const isLinkedIn = draft.channel === "linkedin";
  const blocked = proposal?.policy_verdict === "BLOCKED";
  const approvalState = proposal?.approval?.state ?? "PENDING";
  const executionStatus = proposal?.execution?.status ?? null;
  const linkedInChannel = prospect.contact_channels.find((c) => c.channel === "linkedin") ?? null;

  return (
    <div className="rounded border border-zinc-800 bg-zinc-950/40 p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
        <Badge tone="indigo">{draft.channel}</Badge>
        <span>{isLinkedIn ? "LINKEDIN_COPY_AND_OPEN" : "EMAIL_SEND"}</span>
        {proposal && <Badge tone={POLICY_TONE[proposal.policy_verdict]}>{proposal.policy_verdict}</Badge>}
        {proposal && <Badge tone={APPROVAL_TONE[approvalState]}>{approvalState}</Badge>}
        {executionStatus && <Badge tone={EXECUTION_TONE[executionStatus] ?? "neutral"}>{executionStatus}</Badge>}
      </div>

      {draft.subject && <p className="mt-2 text-sm font-medium text-zinc-100">{draft.subject}</p>}
      <p className="mt-1 whitespace-pre-wrap text-sm text-zinc-300">{draft.body}</p>

      {!proposal ? (
        <div className="mt-3">
          <Button variant="primary" onClick={handlePropose} disabled={pending !== null}>
            {pending === "propose" ? "Proposing…" : "Propose action"}
          </Button>
        </div>
      ) : (
        <div className="mt-3 flex flex-col gap-2 border-t border-zinc-800 pt-3 text-xs">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-zinc-500">
            <span>
              Content hash: <span className="font-mono text-zinc-300">{proposal.content_hash}</span>
            </span>
            {proposal.sender_identifier && (
              <span>
                Sending identity: <span className="font-mono text-zinc-300">{proposal.sender_identifier}</span>
              </span>
            )}
            {proposal.recipient_identifier && (
              <span>
                Recipient: <span className="font-mono text-zinc-300">{proposal.recipient_identifier}</span>
              </span>
            )}
          </div>

          {blocked && (
            <div className="rounded border border-rose-900 bg-rose-950/40 p-2 text-rose-300">
              <p className="font-medium">Blocked — no override is available for this proposal.</p>
              <ul className="mt-1 list-disc pl-4">
                {proposal.blocked_reasons.map((reason) => (
                  <li key={reason}>{reasonCopy(reason)}</li>
                ))}
              </ul>
            </div>
          )}

          {!blocked && approvalState === "PENDING" && (
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="primary" onClick={handleApprove} disabled={pending !== null}>
                {pending === "approve" ? "Approving…" : "Approve"}
              </Button>
              <input
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Reason for rejecting…"
                className="min-w-[180px] flex-1 rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-200 placeholder:text-zinc-600"
              />
              <Button variant="secondary" onClick={handleReject} disabled={pending !== null}>
                {pending === "reject" ? "Rejecting…" : "Reject"}
              </Button>
            </div>
          )}

          {approvalState === "APPROVED" && !executionStatus && (
            <div>
              <Button variant="primary" onClick={handleExecute} disabled={pending !== null}>
                {pending === "execute" ? "Executing…" : "Execute"}
              </Button>
            </div>
          )}

          {executionStatus === "SUCCEEDED" && !isLinkedIn && proposal.execution?.provider_message_id && (
            <p className="text-zinc-500">
              Sent — message id: <span className="font-mono text-zinc-300">{proposal.execution.provider_message_id}</span>
            </p>
          )}

          {executionStatus === "SUCCEEDED" && isLinkedIn && (
            <LinkedInOpenAction
              prospect={prospect}
              linkedInChannel={linkedInChannel}
              openPanel={openPanel}
              copied={copied}
              onCopy={handleCopy}
              onToggleOpenPanel={() => setOpenPanel((v) => !v)}
            />
          )}

          {error && <p className="text-rose-400">{error}</p>}

          {executionStatus && <ActionAuditPanel proposalId={proposal.id} />}
        </div>
      )}
      {!proposal && error && <p className="mt-2 text-xs text-rose-400">{error}</p>}
    </div>
  );
}

export function ActionApprovalPanel({ prospect }: { prospect: ProspectAggregate }) {
  if (prospect.drafts.length === 0) {
    return (
      <p className="p-4 text-sm text-zinc-500">
        No outreach was drafted for this prospect — there is nothing to propose an action for yet.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      {prospect.drafts.map((draft) => (
        <ActionCard key={draft.id} draft={draft} prospect={prospect} />
      ))}
    </div>
  );
}
