import Link from "next/link";
import { Progress } from "@/components/ui/Progress";
import { formatPercent } from "@/lib/format";
import type { GuardrailMetric, ProspectSummary } from "@/lib/types";

const CHECK_LABEL: Record<string, string> = {
  claim_grounding: "Claim grounding",
  no_fabricated_contact: "No fabricated contact",
  cross_prospect_leak: "No cross-prospect leak",
  no_placeholders: "No placeholders",
  duplicate_account: "Duplicate account",
  score_support: "Score support",
  confidence_floor: "Confidence floor",
};

function companyOf(prospects: ProspectSummary[] | null, id: string): string {
  return prospects?.find((p) => p.id === id)?.company_name ?? id.slice(0, 8);
}

/**
 * All seven deterministic review checks (§14) with real pass rates over
 * this run's `review_results` rows — the same checks ReviewPanel renders
 * per prospect, aggregated here across every prospect that reached review.
 * Clicking a failed prospect goes straight to its Review panel.
 */
export function GuardrailPanel({
  guardrails,
  prospects,
}: {
  guardrails: GuardrailMetric[];
  prospects: ProspectSummary[] | null;
}) {
  if (guardrails.length === 0) {
    return (
      <p className="p-4 text-sm text-zinc-500">
        No prospect has reached the review step yet — guardrail pass rates appear once at least one
        has.
      </p>
    );
  }

  return (
    <div className="flex flex-col divide-y divide-zinc-800/70 p-4">
      {guardrails.map((g) => (
        <div key={g.id} className="flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-medium text-zinc-100">{CHECK_LABEL[g.id] ?? g.id}</span>
            <div className="flex items-center gap-2">
              <Progress
                value={g.pass_rate}
                max={1}
                tone={g.pass_rate === 1 ? "emerald" : g.pass_rate >= 0.5 ? "amber" : "rose"}
                className="w-28"
              />
              <span className="font-mono text-xs tabular-nums text-zinc-400">
                {g.passed}/{g.total} · {formatPercent(g.pass_rate)}
              </span>
            </div>
          </div>
          {g.failed_prospect_ids.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-zinc-500">
              <span>failed —</span>
              {g.failed_prospect_ids.map((id) => (
                <Link
                  key={id}
                  href={`/prospects/${id}`}
                  className="font-mono text-rose-400 hover:text-rose-300"
                >
                  {companyOf(prospects, id)}
                </Link>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
