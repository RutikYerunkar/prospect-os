import { Badge } from "@/components/ui/Badge";
import { Stat } from "@/components/ui/Stat";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { formatConfidence } from "@/lib/format";
import type { ProspectScore } from "@/lib/types";

function formatRaw(value: number): string {
  return value.toFixed(2);
}

function formatWeight(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatPoints(value: number): string {
  const points = value * 100;
  return `${points >= 0 ? "+" : ""}${points.toFixed(1)}`;
}

/**
 * The deterministic ICP rubric, rendered as arithmetic: dimension, raw,
 * weight, contribution, evidence count, supported/unsupported — reconciled
 * against the displayed overall score, not just asserted.
 */
export function ScoreBreakdown({ score }: { score: ProspectScore | null }) {
  if (!score) {
    return (
      <p className="p-6 text-sm text-zinc-500">
        No score computed — the pipeline stopped before the Score step ran for this prospect.
      </p>
    );
  }

  const rubricTotal = score.dimensions.reduce((sum, d) => sum + d.contribution, 0);
  const rubricOverall = Math.round(rubricTotal * 100);
  const wasCapped = score.disqualified && rubricOverall !== score.overall;
  const supported = score.dimensions.filter((d) => !d.unsupported).length;

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-end gap-x-10 gap-y-3">
        <Stat label="ICP score" value={score.overall} tone={score.disqualified ? "rose" : "indigo"} />
        <Stat
          label="Confidence (separate from score)"
          value={formatConfidence(score.confidence)}
          tone={score.confidence < 0.6 ? "amber" : undefined}
        />
        <Stat label="Evidence support" value={`${supported} / ${score.dimensions.length} dimensions`} />
        <Stat label="Rubric" value={score.rubric_version} />
      </div>

      <Table>
        <THead>
          <TR>
            <TH>Dimension</TH>
            <TH>Raw</TH>
            <TH>Weight</TH>
            <TH>Contribution</TH>
            <TH>Evidence</TH>
            <TH>Support</TH>
          </TR>
        </THead>
        <TBody>
          {score.dimensions.map((d) => (
            <TR key={d.name}>
              <TD className="font-medium text-zinc-100">{d.name.replaceAll("_", " ")}</TD>
              <TD className="font-mono tabular-nums text-zinc-300">{formatRaw(d.raw)}</TD>
              <TD className="font-mono tabular-nums text-zinc-400">{formatWeight(d.weight)}</TD>
              <TD className="font-mono tabular-nums text-zinc-100">{formatPoints(d.contribution)}</TD>
              <TD className="font-mono tabular-nums text-zinc-400">{d.evidence_ids.length}</TD>
              <TD>
                {d.unsupported ? (
                  <Badge tone="rose">unsupported</Badge>
                ) : (
                  <Badge tone="emerald">supported</Badge>
                )}
              </TD>
            </TR>
          ))}
          <TR className="border-t border-zinc-700">
            <TD className="font-medium text-zinc-400">Σ weight × raw × 100</TD>
            <TD />
            <TD />
            <TD className="font-mono tabular-nums text-zinc-100">{formatPoints(rubricTotal)}</TD>
            <TD />
            <TD />
          </TR>
        </TBody>
      </Table>

      {score.modifiers.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Modifiers</h3>
          {score.modifiers.map((m) => (
            <div key={m.name} className="rounded border border-amber-900 bg-amber-950/40 px-3 py-2 text-sm">
              <span className="font-medium text-amber-400">{m.name.replaceAll("_", " ")}</span>
              <span className="text-zinc-400"> — {m.reason}</span>
              {m.detail && <div className="mt-0.5 font-mono text-xs text-zinc-500">{m.detail}</div>}
            </div>
          ))}
        </div>
      )}

      <p className="font-mono text-xs text-zinc-500">
        {wasCapped
          ? `rubric total ${rubricOverall} → capped to ${score.overall} by the modifier above`
          : `dimension contributions sum to ${rubricOverall}, matching the displayed score of ${score.overall}`}
      </p>

      {score.explanation && <p className="max-w-2xl text-sm text-zinc-300">{score.explanation}</p>}
    </div>
  );
}
