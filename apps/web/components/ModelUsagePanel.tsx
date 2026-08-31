import { Badge } from "@/components/ui/Badge";
import type { LLMUsage } from "@/lib/types";

function Cell({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div title={hint} className="flex flex-col gap-1 rounded border border-zinc-800 bg-zinc-950/40 px-3 py-2.5">
      <span className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</span>
      <span className="font-mono text-lg tabular-nums leading-none text-zinc-100">{value}</span>
    </div>
  );
}

/**
 * Model Usage & Cost — Checkpoint G Quality tab addition. Backed only by
 * `/evaluation`'s `llm_usage` block (computed on read from `llm_calls`, one
 * row per provider attempt); never hardcodes a token or dollar figure.
 */
export function ModelUsagePanel({ usage }: { usage: LLMUsage }) {
  if (usage.provider_attempts === 0) {
    return (
      <div className="px-4 py-3">
        <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Model usage &amp; cost</h3>
        <p className="mt-2 text-sm text-zinc-500">No LLM calls recorded for this run yet.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 px-4 py-4">
      <h3
        title="Computed on read from llm_calls — one row per provider attempt, grouped into logical calls by call_group_id."
        className="w-fit text-xs font-medium uppercase tracking-wide text-zinc-500"
      >
        Model usage &amp; cost
      </h3>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
        <Cell label="Logical calls" value={String(usage.logical_calls)} />
        <Cell label="Provider attempts" value={String(usage.provider_attempts)} />
        <Cell label="Tokens in" value={usage.tokens_in.toLocaleString()} />
        <Cell label="Tokens out" value={usage.tokens_out.toLocaleString()} />
        <Cell
          label="Reasoning tokens"
          value={usage.reasoning_tokens == null ? "—" : usage.reasoning_tokens.toLocaleString()}
          hint="Only exposed when the provider reports it."
        />
        <Cell
          label="Estimated cost"
          value={usage.estimated_cost_usd == null ? "—" : `$${usage.estimated_cost_usd.toFixed(4)}`}
          hint="Null whenever pricing wasn't configured for any contributing attempt — never a partial guess."
        />
      </div>
      <div className="flex flex-wrap items-center gap-4 text-xs text-zinc-500">
        <span>
          Transport retries: <span className="font-mono text-zinc-300">{usage.transport_retries}</span>
        </span>
        <span>
          Schema repairs: <span className="font-mono text-zinc-300">{usage.schema_repairs}</span>
        </span>
        {usage.budget_tripped && <Badge tone="amber">soft budget tripped during this run</Badge>}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(usage.by_operation).map(([op, stats]) => (
          <Badge key={op} tone="neutral" mono>
            {op} · {stats.attempts} attempt{stats.attempts === 1 ? "" : "s"}
          </Badge>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(usage.by_status).map(([status, count]) => (
          <Badge key={status} tone={status === "OK" ? "emerald" : "rose"} mono>
            {status} · {count}
          </Badge>
        ))}
      </div>
    </div>
  );
}
