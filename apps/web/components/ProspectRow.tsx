import { TD, TR } from "@/components/ui/Table";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { formatConfidence, formatScore, formatStage, formatStatus } from "@/lib/format";
import { PIPELINE_STAGES, type ProspectSummary } from "@/lib/types";
import type { RetryInfo } from "@/lib/useRunStream";

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

function StageTrack({ stage, terminal }: { stage: string; terminal: boolean }) {
  if (stage === "DISCOVERED" && !terminal) {
    return <span className="text-xs text-zinc-600">queued</span>;
  }
  const currentIndex = terminal ? PIPELINE_STAGES.length : PIPELINE_STAGES.indexOf(stage as never);
  return (
    <div className="flex items-center gap-1" aria-label={`stage: ${formatStage(stage)}`}>
      {PIPELINE_STAGES.map((s, i) => {
        const done = terminal || i < currentIndex;
        const current = !terminal && i === currentIndex;
        return (
          <span
            key={s}
            title={s}
            className={`h-1.5 w-3.5 rounded-full ${
              done ? "bg-indigo-400" : current ? "bg-sky-400 animate-pulse" : "bg-zinc-800"
            }`}
          />
        );
      })}
    </div>
  );
}

export function ProspectRow({
  prospect,
  retry,
  onOpen,
}: {
  prospect: ProspectSummary;
  retry?: RetryInfo;
  onOpen: (id: string) => void;
}) {
  const terminal = prospect.status !== "RUNNING" && prospect.status !== "PENDING";

  return (
    <TR onClick={() => onOpen(prospect.id)}>
      <TD>
        <div className="flex flex-col">
          <span className="font-medium text-zinc-100">{prospect.company_name}</span>
          {prospect.company_domain && (
            <span className="font-mono text-xs text-zinc-500">{prospect.company_domain}</span>
          )}
        </div>
      </TD>
      <TD>
        <div className="flex flex-col gap-1.5">
          <span className="font-mono text-xs text-zinc-400">
            {terminal ? "done" : formatStage(prospect.stage).toLowerCase()}
          </span>
          <StageTrack stage={prospect.stage} terminal={terminal} />
        </div>
      </TD>
      <TD>
        {retry ? (
          <Badge tone="amber" mono>
            {retry.step} · retry {retry.attempt}
          </Badge>
        ) : prospect.had_retry ? (
          <Badge tone="amber">↻ retried</Badge>
        ) : (
          <span className="text-zinc-700">—</span>
        )}
      </TD>
      <TD className="max-w-[220px] truncate text-zinc-400">{prospect.top_signal ?? "—"}</TD>
      <TD>
        {prospect.contact_verification ? (
          <div className="flex flex-col">
            <span className="text-zinc-300">{prospect.contact_name ?? "—"}</span>
            <span className="text-xs text-zinc-500">{prospect.contact_verification}</span>
          </div>
        ) : (
          <span className="text-zinc-700">—</span>
        )}
      </TD>
      <TD className="font-mono tabular-nums">{formatScore(prospect.icp_score)}</TD>
      <TD className="font-mono tabular-nums text-zinc-400">{formatConfidence(prospect.confidence)}</TD>
      <TD>
        <Badge tone={STATUS_TONE[prospect.status] ?? "neutral"}>{formatStatus(prospect.status)}</Badge>
      </TD>
    </TR>
  );
}
