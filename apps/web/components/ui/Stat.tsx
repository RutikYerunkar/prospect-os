import { cn } from "@/lib/cn";

export function Stat({
  label,
  value,
  tone,
  className,
}: {
  label: string;
  value: string | number;
  tone?: "emerald" | "amber" | "rose" | "sky" | "indigo";
  className?: string;
}) {
  const toneClass: Record<string, string> = {
    emerald: "text-emerald-400",
    amber: "text-amber-400",
    rose: "text-rose-400",
    sky: "text-sky-400",
    indigo: "text-indigo-400",
  };
  return (
    <div className={cn("flex flex-col gap-0.5", className)}>
      <span className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</span>
      <span
        className={cn(
          "font-mono text-lg tabular-nums leading-none",
          tone ? toneClass[tone] : "text-zinc-100",
        )}
      >
        {value}
      </span>
    </div>
  );
}
