import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { formatDuration } from "@/lib/format";
import type { AgentTaskTrace } from "@/lib/types";

const STATUS_TONE: Record<string, BadgeTone> = {
  OK: "emerald",
  RETRY: "amber",
  TIMEOUT: "amber",
  FAILED: "rose",
  SKIPPED: "neutral",
};

/**
 * One row per attempt — a polished table, not a graphical waterfall.
 * Retries are separate rows by construction (`agent_tasks` records one row
 * per attempt), so a retry-then-success sequence stays visible rather than
 * collapsing into a single "eventually worked" line.
 */
export function TraceTable({ trace }: { trace: AgentTaskTrace[] }) {
  if (trace.length === 0) {
    return <p className="p-4 text-sm text-zinc-500">No execution trace recorded for this prospect.</p>;
  }

  const sorted = [...trace].sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime());
  const maxDuration = Math.max(...sorted.map((t) => t.duration_ms ?? 0), 1);

  return (
    <Table>
      <THead>
        <TR>
          <TH>Step</TH>
          <TH>Attempt</TH>
          <TH>Status</TH>
          <TH>Duration</TH>
          <TH>Provider / model</TH>
          <TH>Evidence</TH>
          <TH>Error</TH>
        </TR>
      </THead>
      <TBody>
        {sorted.map((t) => (
          <TR key={t.id}>
            <TD className="font-medium text-zinc-100">{t.step_name}</TD>
            <TD className="font-mono tabular-nums text-zinc-400">{t.attempt}</TD>
            <TD>
              <Badge tone={STATUS_TONE[t.status] ?? "neutral"}>{t.status}</Badge>
            </TD>
            <TD>
              <div className="flex items-center gap-2">
                <span className="w-14 shrink-0 font-mono text-xs tabular-nums text-zinc-400">
                  {t.duration_ms != null ? formatDuration(t.duration_ms) : "—"}
                </span>
                {t.duration_ms != null && (
                  <span className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-zinc-800">
                    <span
                      className="block h-full rounded-full bg-indigo-400"
                      style={{ width: `${Math.min(100, (t.duration_ms / maxDuration) * 100)}%` }}
                    />
                  </span>
                )}
              </div>
            </TD>
            <TD className="text-xs text-zinc-400">{[t.provider, t.model].filter(Boolean).join(" · ") || "—"}</TD>
            <TD className="font-mono tabular-nums text-zinc-400">{t.evidence_count ?? "—"}</TD>
            <TD className="max-w-[240px] truncate text-xs text-rose-400" title={t.error_message ?? undefined}>
              {t.error_type ? `${t.error_type}${t.error_message ? `: ${t.error_message}` : ""}` : "—"}
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
