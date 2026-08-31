import type { ReactNode } from "react";
import { Panel } from "@/components/ui/Panel";
import { Badge } from "@/components/ui/Badge";
import type { PlaySpec } from "@/lib/types";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</dt>
      <dd className="text-sm text-zinc-200">{children}</dd>
    </div>
  );
}

function ChipList({ items }: { items: string[] }) {
  if (items.length === 0) return <span className="text-zinc-600">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((item) => (
        <Badge key={item} tone="indigo">
          {item}
        </Badge>
      ))}
    </div>
  );
}

/**
 * Read-only render of a parsed `PlaySpec` — proof that the natural-language
 * objective became structured execution criteria. No Objective Parser LLM
 * agent exists yet (Checkpoint C's honest deviation): this spec was built
 * deterministically by `POST /api/plays` from the objective + the four form
 * controls, not invented by this component.
 */
export function PlanPanel({ spec }: { spec: PlaySpec }) {
  return (
    <Panel title="Parsed play spec" bodyClassName="p-4">
      <dl className="grid grid-cols-2 gap-4">
        <Field label="Target industries">
          <ChipList items={spec.target_industries} />
        </Field>
        <Field label="Excluded industries">
          <ChipList items={spec.excluded_industries} />
        </Field>
        <Field label="Size band">
          <span className="font-mono">
            {spec.size_band_min}–{spec.size_band_max} employees
          </span>
        </Field>
        <Field label="Prospect count">
          <span className="font-mono">{spec.target_count}</span>
        </Field>
        <Field label="Minimum ICP score">
          <span className="font-mono">{spec.min_score}</span>
        </Field>
        <Field label="Minimum confidence">
          <span className="font-mono">{spec.min_confidence}</span>
        </Field>
        <Field label="Target funding stages">
          <ChipList items={spec.target_funding_stages} />
        </Field>
        <Field label="Target technologies">
          <ChipList items={spec.target_technologies} />
        </Field>
        <Field label="Persona titles">
          <ChipList items={spec.persona_titles} />
        </Field>
      </dl>
    </Panel>
  );
}
