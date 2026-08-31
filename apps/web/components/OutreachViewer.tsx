import { Badge } from "@/components/ui/Badge";
import type { EvidenceItem, OutreachDraft } from "@/lib/types";

export function OutreachViewer({
  drafts,
  evidenceById,
}: {
  drafts: OutreachDraft[];
  evidenceById: Record<string, EvidenceItem>;
}) {
  if (drafts.length === 0) {
    return (
      <p className="p-4 text-sm text-zinc-500">
        No outreach was drafted for this prospect — personalization was skipped or the pipeline
        stopped before it ran.
      </p>
    );
  }

  const sorted = [...drafts].sort((a, b) => a.step_index - b.step_index);

  return (
    <div className="flex flex-col gap-4 p-4">
      {sorted.map((draft) => (
        <div key={draft.id} className="rounded border border-zinc-800 bg-zinc-950/40 p-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
            <Badge tone="indigo">{draft.channel}</Badge>
            <span>step {draft.step_index + 1}</span>
            <span className="font-mono">v{draft.version}</span>
            <Badge tone="neutral">{draft.status}</Badge>
          </div>
          {draft.subject && <p className="mt-2 text-sm font-medium text-zinc-100">{draft.subject}</p>}
          <p className="mt-1.5 whitespace-pre-wrap text-sm text-zinc-300">{draft.body}</p>
          {draft.claim_map.length > 0 && (
            <div className="mt-3 flex flex-col gap-1.5 border-t border-zinc-800 pt-2.5">
              <h4 className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                Grounded claim references
              </h4>
              {draft.claim_map.map((c, i) => (
                <div key={i} className="text-xs text-zinc-500">
                  <span className="text-zinc-400">&ldquo;{c.sentence}&rdquo;</span>
                  {c.evidence_ids.length > 0 ? (
                    <span> — {c.evidence_ids.map((id) => evidenceById[id]?.title ?? id).join(", ")}</span>
                  ) : (
                    <Badge tone="rose" className="ml-1.5">
                      unsupported
                    </Badge>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
