import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { formatConfidence, formatDateOnly } from "@/lib/format";
import type { EvidenceItem, SignalItem } from "@/lib/types";

const SIGNAL_TONE: Record<string, BadgeTone> = {
  FUNDING: "emerald",
  HIRING: "sky",
  TECH: "indigo",
  LEADERSHIP: "amber",
  PRODUCT: "neutral",
};

export function SignalList({
  signals,
  evidenceById,
}: {
  signals: SignalItem[];
  evidenceById: Record<string, EvidenceItem>;
}) {
  if (signals.length === 0) {
    return <p className="p-4 text-sm text-zinc-500">No signals detected for this prospect.</p>;
  }

  // Research can extract several structured facts (e.g. three GTM hiring
  // roles) from one source sentence, each recorded as its own Signal row —
  // real data, not a rendering bug. Group identical (type, summary) pairs
  // for display so a founder sees "3 roles found", not three copy-pasted
  // lines with the same text.
  const grouped = new Map<string, { signal: (typeof signals)[number]; count: number }>();
  for (const s of signals) {
    const key = `${s.type}|${s.summary}`;
    const existing = grouped.get(key);
    if (existing) existing.count += 1;
    else grouped.set(key, { signal: s, count: 1 });
  }

  return (
    <ul className="divide-y divide-zinc-800/70">
      {[...grouped.values()].map(({ signal: s, count }) => {
        const grounded = s.evidence_ids.length > 0;
        return (
          <li key={s.id} className="flex flex-col gap-1.5 px-4 py-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={SIGNAL_TONE[s.type] ?? "neutral"}>{s.type}</Badge>
              <span className="text-zinc-100">{s.summary}</span>
              {count > 1 && (
                <Badge tone="neutral" mono>
                  ×{count}
                </Badge>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
              <span>{formatDateOnly(s.occurred_at)}</span>
              <span>confidence {formatConfidence(s.confidence)}</span>
              {grounded ? (
                <span>
                  supported by {s.evidence_ids.map((id) => evidenceById[id]?.title ?? id).join(", ")}
                </span>
              ) : (
                <Badge tone="rose">ungrounded — demoted, does not score</Badge>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
