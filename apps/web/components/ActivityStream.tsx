import { useEffect, useRef } from "react";
import { formatTime } from "@/lib/format";
import type { ProspectSummary, RunEvent } from "@/lib/types";

function describeEvent(event: RunEvent, companyOf: (id: string | null) => string): string {
  const company = companyOf(event.prospect_id);
  const p = event.payload;

  switch (event.type) {
    case "run.started":
      return `run started · mode ${p.mode} · seed ${p.seed}`;
    case "run.completed":
      return `run completed · ${p.status}`;
    case "run.failed":
      return `run failed · ${p.error}`;
    case "prospect.discovered":
      return `${company} · discovered`;
    case "step.started":
      return `${company} · ${p.step} started`;
    case "step.completed":
      if (p.skipped) return `${company} · ${p.step} skipped (degraded)`;
      if (p.ok === false) return `${company} · ${p.step} failed`;
      return `${company} · ${p.step} completed`;
    case "step.retrying":
      return `${company} · provider ${p.error_type} on ${p.step} · retrying (attempt ${p.attempt})`;
    case "prospect.scored":
      return `${company} · score computed — ${p.overall}${p.disqualified ? " (disqualified)" : ""}`;
    case "prospect.reviewed":
      return `${company} · review verdict ${p.verdict}`;
    case "prospect.completed":
      return p.error ? `${company} · failed — ${p.error}` : `${company} · completed — ${p.status}`;
    default:
      return `${company} · ${event.type}`;
  }
}

export function ActivityStream({
  events,
  prospects,
}: {
  events: RunEvent[];
  prospects: ProspectSummary[] | null;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  function companyOf(id: string | null): string {
    if (!id) return "run";
    return prospects?.find((p) => p.id === id)?.company_name ?? id.slice(0, 8);
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [events.length]);

  const newestFirst = [...events].reverse();

  return (
    <div ref={scrollRef} className="max-h-[520px] overflow-y-auto">
      {newestFirst.length === 0 ? (
        <p className="p-4 text-sm text-zinc-500">No activity yet.</p>
      ) : (
        <ul className="divide-y divide-zinc-800/70">
          {newestFirst.map((event) => (
            <li key={event.seq} className="flex items-start gap-3 px-4 py-2 text-xs">
              <span className="mt-0.5 shrink-0 font-mono text-zinc-600">{formatTime(event.ts)}</span>
              <span className="text-zinc-300">{describeEvent(event, companyOf)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
