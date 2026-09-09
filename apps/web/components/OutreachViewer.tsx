import { Badge } from "@/components/ui/Badge";
import type { EvidenceItem, OutreachDraft } from "@/lib/types";

// v2 §V2-F — deterministic group order, EMAIL first. An unrecognized future
// channel sorts after every known one (alphabetically among themselves),
// never crashes and never silently drops a draft.
const CHANNEL_ORDER: Record<string, number> = { email: 0, linkedin: 1 };

function channelRank(channel: string): number {
  return channel in CHANNEL_ORDER ? CHANNEL_ORDER[channel] : Object.keys(CHANNEL_ORDER).length;
}

function groupByChannel(drafts: OutreachDraft[]): Array<[string, OutreachDraft[]]> {
  const groups = new Map<string, OutreachDraft[]>();
  for (const draft of drafts) {
    const existing = groups.get(draft.channel);
    if (existing) {
      existing.push(draft);
    } else {
      groups.set(draft.channel, [draft]);
    }
  }
  return [...groups.entries()]
    .sort(([a], [b]) => channelRank(a) - channelRank(b) || a.localeCompare(b))
    .map(([channel, channelDrafts]): [string, OutreachDraft[]] => [
      channel,
      [...channelDrafts].sort((a, b) => a.step_index - b.step_index),
    ]);
}

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

  const groups = groupByChannel(drafts);

  return (
    <div className="flex flex-col gap-5 p-4">
      {groups.map(([channel, channelDrafts]) => (
        <div key={channel} className="flex flex-col gap-3">
          <h3 className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">{channel}</h3>
          {channelDrafts.map((draft) => (
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
      ))}
    </div>
  );
}
