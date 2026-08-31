import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import type { EvidenceItem } from "@/lib/types";

const ORIGIN_LABEL: Record<string, string> = {
  DEMO_FIXTURE: "Synthetic evidence · demo fixture",
  LLM_INFERENCE: "Model inference · unsourced",
};

/**
 * One evidence item. Provenance decides the rendering, not a UI convention:
 * only LIVE_FETCH ever gets a clickable `<a>` — the schema itself forbids a
 * `source_url` on any other origin (§12), so this mirrors a real invariant
 * rather than trusting the backend silently.
 */
export function EvidenceCard({ evidence }: { evidence: EvidenceItem }) {
  const isLive = evidence.origin === "LIVE_FETCH" && !!evidence.source_url;
  const isInferred = evidence.origin === "LLM_INFERENCE";

  return (
    <div
      className={cn(
        "rounded border px-3 py-2.5 text-sm",
        isInferred ? "border-dashed border-zinc-700 bg-zinc-900/40" : "border-zinc-800 bg-zinc-900",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-zinc-100">{evidence.title}</span>
        <div className="flex items-center gap-1.5">
          {evidence.signal_type && <Badge tone="indigo">{evidence.signal_type}</Badge>}
          <Badge tone="neutral" mono>
            {Math.round(evidence.confidence * 100)}% conf.
          </Badge>
        </div>
      </div>
      <p className="mt-1.5 text-zinc-300">{evidence.claim}</p>
      <blockquote className="mt-1.5 border-l-2 border-zinc-700 pl-2.5 text-xs text-zinc-500 italic">
        &ldquo;{evidence.snippet}&rdquo;
      </blockquote>
      <div className="mt-2">
        {isLive ? (
          <a
            href={evidence.source_url!}
            target="_blank"
            rel="noreferrer noopener"
            className="font-mono text-xs text-indigo-400 hover:text-indigo-300"
          >
            {evidence.source_url} ↗
          </a>
        ) : (
          <span className="inline-flex items-center gap-1 text-xs text-zinc-600">
            {ORIGIN_LABEL[evidence.origin] ?? evidence.origin}
            {evidence.retrieved_at && ` · ${evidence.retrieved_at}`}
          </span>
        )}
      </div>
    </div>
  );
}
