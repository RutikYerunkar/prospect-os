import { Badge } from "@/components/ui/Badge";
import { formatPercent } from "@/lib/format";
import type { SearchQualityMetrics } from "@/lib/types";

function Cell({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "amber";
}) {
  return (
    <div title={hint} className="flex flex-col gap-1 rounded border border-zinc-800 bg-zinc-950/40 px-3 py-2.5">
      <span className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</span>
      <span className={`font-mono text-lg tabular-nums leading-none ${tone === "amber" ? "text-amber-400" : "text-zinc-100"}`}>
        {value}
      </span>
    </div>
  );
}

/**
 * Search — H2 Quality tab addition. Backed only by `/evaluation`'s
 * `search_quality` block (computed on read from `search_calls`/
 * `source_documents`/`run_events`); never hardcodes a count or dollar
 * figure. Renders the same "no data yet" shape for a Demo run (zero search
 * calls recorded — the demo fixture path never issues one) as for a Live
 * run that hasn't reached discovery yet.
 */
export function SearchQualityPanel({ search }: { search: SearchQualityMetrics }) {
  if (search.search_calls === 0 && search.result_occurrences === 0) {
    return (
      <div className="px-4 py-3">
        <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Search</h3>
        <p className="mt-2 text-sm text-zinc-500">No search calls recorded for this run.</p>
      </div>
    );
  }

  const rejectionEntries = Object.entries(search.discovery_rejection_reasons);
  const domainMethodEntries = Object.entries(search.domain_resolution_method_counts);
  const errorEntries = Object.entries(search.search_error_counts);

  return (
    <div className="flex flex-col gap-3 px-4 py-4">
      <h3
        title="Computed on read from search_calls/source_documents (one row per provider call attempt / retrieval occurrence) and run_events (discovery narrative)."
        className="w-fit text-xs font-medium uppercase tracking-wide text-zinc-500"
      >
        Search
      </h3>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
        <Cell label="Result occurrences" value={String(search.result_occurrences)} hint="Every source_documents row for this run, before dedupe." />
        <Cell label="Unique sources" value={String(search.sources_retrieved_unique)} hint="Distinct winning sources after dedupe (is_winner=True)." />
        <Cell label="Used as evidence" value={String(search.sources_used_as_evidence)} hint="Winners whose evidence_id resolved to a real, persisted Evidence row." />
        <Cell label="Source utilization" value={formatPercent(search.source_utilization_rate)} hint="sources_used_as_evidence ÷ sources_retrieved_unique." />
        <Cell label="Duplicate retrieval" value={formatPercent(search.duplicate_retrieval_rate)} hint="1 − (unique sources ÷ result occurrences)." />
        <Cell label="Search calls" value={String(search.search_calls)} hint="Every real provider call attempt (discovery + domain resolution + per-company retrieval + extraction)." />
        <Cell label="Extraction calls" value={String(search.extraction_calls)} hint="Batched Tavily extract() calls (one per prospect's winning sources)." />
        <Cell label="Partial extractions" value={String(search.partial_extractions)} tone={search.partial_extractions > 0 ? "amber" : undefined} hint="Extract calls where some (not all) URLs in the batch failed — degraded, not fatal." />
        <Cell label="Failed/partial sources" value={String(search.failed_or_partial_sources)} hint="source_documents rows whose retrieval status is failed or partial." />
        <Cell label="p50 search latency" value={search.p50_search_latency_ms === null ? "—" : `${Math.round(search.p50_search_latency_ms)}ms`} />
        <Cell label="p95 search latency" value={search.p95_search_latency_ms === null ? "—" : `${Math.round(search.p95_search_latency_ms)}ms`} />
        <Cell
          label="Estimated cost"
          value={search.search_cost_usd == null ? "—" : `$${search.search_cost_usd.toFixed(4)}`}
          hint="Null unless every contributing call has a computed cost — never a partial guess. Hard call/query caps are the real safety control."
        />
        <Cell
          label="Credits used"
          value={search.search_credits_used == null ? "—" : String(search.search_credits_used)}
          hint="Provider-native usage figure from Tavily's include_usage response field, independent of whether a USD rate is configured."
        />
        <Cell label="Industry grounded" value={formatPercent(search.industry_grounded_coverage)} hint="Share of scored prospects whose industry_fit dimension is independently grounded (SUPPORTED)." />
        <Cell label="Employee count grounded" value={formatPercent(search.employee_count_grounded_coverage)} hint="Share of scored prospects whose size_fit dimension is independently grounded (SUPPORTED)." />
        <Cell label="Unevaluable exclusion" value={String(search.unevaluable_exclusion_count)} hint="Prospects whose exclusion policy could not be evaluated at all (industry never grounded) — forces NEEDS_REVIEW." />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="flex flex-col gap-2">
          <h4 title="Reason a discovery candidate was dropped before becoming a prospect." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Discovery rejection reasons
          </h4>
          {rejectionEntries.length === 0 ? (
            <span className="text-xs text-zinc-500">none — every candidate resolved</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {rejectionEntries.map(([reason, count]) => (
                <Badge key={reason} tone="amber" mono>
                  {reason} · {count}
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <h4 title="Whether a company's canonical domain was accepted deterministically (one safe, label-matching candidate) or via the bounded DOMAIN_SELECTION LLM fallback." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Domain resolution method
          </h4>
          {domainMethodEntries.length === 0 ? (
            <span className="text-xs text-zinc-500">no domains resolved yet</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {domainMethodEntries.map(([method, count]) => (
                <Badge key={method} tone={method === "deterministic" ? "emerald" : "indigo"} mono>
                  {method} · {count}
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <h4 title="Search-call retries and provider errors by type." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Retries &amp; errors
          </h4>
          <span className="text-xs text-zinc-500">
            Retries: <span className="font-mono text-zinc-300">{search.search_retries}</span>
          </span>
          {errorEntries.length === 0 ? (
            <span className="text-xs text-zinc-500">none recorded</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {errorEntries.map(([type, count]) => (
                <Badge key={type} tone="rose" mono>
                  {type} · {count}
                </Badge>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
