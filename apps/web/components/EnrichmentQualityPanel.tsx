import { Badge } from "@/components/ui/Badge";
import { formatPercent } from "@/lib/format";
import type { EnrichmentMetrics } from "@/lib/types";

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
 * V2-J §1 — Enrichment. Backed only by `/evaluation`'s `enrichment` block
 * (computed on read from `enrichment_calls`/`contact_channels`/
 * `contact_enrichments`); never hardcodes a count or rate. A `null` rate
 * renders as "—" (no denominator), NEVER 0% — a run with zero enrichment
 * attempts must not read as "0% found," which would misread as "we looked
 * and found nothing" rather than "nothing was attempted." The five contact
 * axes (person identity, email discovery, email verification, LinkedIn
 * resolution, LinkedIn identity match) stay visible as distinct rates here,
 * never collapsed into a single enrichment status.
 */
export function EnrichmentQualityPanel({ enrichment }: { enrichment: EnrichmentMetrics }) {
  if (enrichment.attempted === 0 && enrichment.not_attempted_budget_count === 0) {
    return (
      <div className="px-4 py-3">
        <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Enrichment</h3>
        <p className="mt-2 text-sm text-zinc-500">No contact enrichment has been attempted for this run.</p>
      </div>
    );
  }

  const identityEntries = Object.entries(enrichment.identity_match_distribution);
  const statusEntries = Object.entries(enrichment.enrichment_attempts_by_status);
  const preservedEntries = Object.entries(enrichment.preserved_last_known_good_breakdown);

  return (
    <div className="flex flex-col gap-3 px-4 py-4">
      <h3
        title="Computed on read from enrichment_calls (one row per provider call attempt) and contact_channels/contact_enrichments (derived per-prospect state and raw observations)."
        className="w-fit text-xs font-medium uppercase tracking-wide text-zinc-500"
      >
        Enrichment
      </h3>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
        <Cell label="Attempted" value={String(enrichment.attempted)} hint="Enrichment call groups that actually reached a provider (excludes NOT_ATTEMPTED_BUDGET)." />
        <Cell label="Matched" value={String(enrichment.matched)} hint="Provider asserted it found the named person at all." />
        <Cell label="Match rate" value={formatPercent(enrichment.match_rate)} hint="matched ÷ attempted." />
        <Cell label="Email found" value={formatPercent(enrichment.email_found_rate)} hint="Email discovery state FOUND ÷ attempted." />
        <Cell label="Email verified" value={formatPercent(enrichment.email_verified_rate)} hint="Email verification state VERIFIED ÷ email FOUND — not diluted by prospects with no email at all." />
        <Cell label="Catch-all" value={formatPercent(enrichment.catch_all_rate)} hint="Share of found emails flagged catch-all by the provider. Unknown (null) observations are excluded from both sides of the rate." />
        <Cell label="LinkedIn resolved" value={formatPercent(enrichment.linkedin_resolved_rate)} hint="LinkedIn discovery state RESOLVED ÷ attempted." />
        <Cell label="Grammar rejections" value={String(enrichment.identifier_grammar_rejections)} tone={enrichment.identifier_grammar_rejections > 0 ? "amber" : undefined} hint="Observed LinkedIn identifiers that failed the origin-scoped identifier grammar and were never surfaced." />
        <Cell label="Provider error rate" value={formatPercent(enrichment.provider_error_rate)} hint="PROVIDER_ERROR attempts ÷ every attempt that actually reached a provider. A successful call that found nothing (NOT_FOUND) is never counted as an error." />
        <Cell label="Budget-skipped" value={String(enrichment.not_attempted_budget_count)} hint="Call groups the per-run enrichment-call budget refused before ever reaching a provider." />
        <Cell label="Stale channels" value={String(enrichment.stale_channel_count)} hint="Channels that WERE observed at least once and have since aged past the staleness threshold — never a channel that was simply never attempted." />
        <Cell label="Preserved last-known-good" value={String(enrichment.preserved_last_known_good_count)} hint="Channels whose CURRENT state was preserved from an earlier successful observation, not produced by the most recent attempt." />
        <Cell label="p50 latency" value={enrichment.p50_enrichment_latency_ms === null ? "—" : `${Math.round(enrichment.p50_enrichment_latency_ms)}ms`} />
        <Cell label="p95 latency" value={enrichment.p95_enrichment_latency_ms === null ? "—" : `${Math.round(enrichment.p95_enrichment_latency_ms)}ms`} />
        <Cell
          label="Estimated cost"
          value={enrichment.enrichment_cost_usd == null ? "—" : `$${enrichment.enrichment_cost_usd.toFixed(4)}`}
          hint="Null unless every contributing call has a computed cost — never a partial guess."
        />
        <Cell
          label="Credits used"
          value={enrichment.enrichment_credits_used == null ? "—" : String(enrichment.enrichment_credits_used)}
          hint="Null when no contributing call reported credits, any contributing credit figure is unknown, or more than one provider's (incomparable) credit unit contributed."
        />
        <Cell label="Provider calls" value={String(enrichment.enrichment_calls)} hint="Every real provider call attempt (excludes NOT_ATTEMPTED_BUDGET)." />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="flex flex-col gap-2">
          <h4 title="LinkedIn identity-match verdict distribution among resolved profiles — person-name and company matched independently." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            LinkedIn identity match
          </h4>
          {identityEntries.length === 0 ? (
            <span className="text-xs text-zinc-500">no LinkedIn observations yet</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {identityEntries.map(([state, count]) => (
                <Badge key={state} tone={state === "STRONG_MATCH" ? "emerald" : state === "MISMATCH" ? "rose" : "neutral"} mono>
                  {state} · {count}
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <h4 title="Every enrichment_calls attempt, by its own status." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Attempts by status
          </h4>
          {statusEntries.length === 0 ? (
            <span className="text-xs text-zinc-500">none recorded</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {statusEntries.map(([status, count]) => (
                <Badge key={status} tone={status === "OK" ? "emerald" : status === "PROVIDER_ERROR" ? "rose" : "neutral"} mono>
                  {status} · {count}
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <h4 title="Why a channel's current state was preserved from an earlier attempt rather than produced by the most recent one." className="w-fit text-[11px] uppercase tracking-wide text-zinc-500">
            Preserved last-known-good
          </h4>
          {preservedEntries.length === 0 ? (
            <span className="text-xs text-zinc-500">none — every current state came from its own latest attempt</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {preservedEntries.map(([reason, count]) => (
                <Badge key={reason} tone="amber" mono>
                  {reason} · {count}
                </Badge>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
