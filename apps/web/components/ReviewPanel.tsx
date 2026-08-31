import { Badge, type BadgeTone } from "@/components/ui/Badge";
import type { ReviewResult } from "@/lib/types";

const VERDICT_TONE: Record<string, BadgeTone> = {
  PASS: "emerald",
  NEEDS_REVIEW: "amber",
  FAIL: "rose",
};

const CHECK_LABEL: Record<string, string> = {
  claim_grounding: "Claim grounding",
  no_fabricated_contact: "No fabricated contact",
  cross_prospect_leak: "No cross-prospect leak",
  no_placeholders: "No placeholders",
  duplicate_account: "Duplicate account",
  score_support: "Score support",
  confidence_floor: "Confidence floor",
};

/**
 * All seven deterministic checks, including passes — showing the work is
 * the point. No LLM anywhere in this path; the verdict is a join, not a
 * judgment.
 */
export function ReviewPanel({ review }: { review: ReviewResult | null }) {
  if (!review) {
    return <p className="p-4 text-sm text-zinc-500">Review did not run for this prospect.</p>;
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-zinc-500">Deterministic verdict</span>
        <Badge tone={VERDICT_TONE[review.verdict] ?? "neutral"}>{review.verdict}</Badge>
      </div>
      <p className="text-xs text-zinc-500">
        Seven policy checks over already-validated structured data — a pass here is a join, not a
        model&apos;s opinion.
      </p>
      <ul className="flex flex-col divide-y divide-zinc-800/70 rounded border border-zinc-800">
        {review.checks.map((c) => (
          <li key={c.id} className="flex flex-col gap-1 px-3 py-2.5 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={c.passed ? "emerald" : c.severity === "hard" ? "rose" : "amber"}>
                {c.passed ? "PASS" : "FAIL"}
              </Badge>
              <span className="font-medium text-zinc-100">{CHECK_LABEL[c.id] ?? c.id}</span>
              <Badge tone="neutral" mono>
                {c.severity}
              </Badge>
            </div>
            <p className="text-zinc-400">{c.detail}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
