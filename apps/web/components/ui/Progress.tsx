import { cn } from "@/lib/cn";

export function Progress({
  value,
  max = 1,
  tone = "indigo",
  className,
}: {
  value: number;
  max?: number;
  tone?: "indigo" | "emerald" | "amber" | "rose" | "sky";
  className?: string;
}) {
  const pct = max > 0 ? Math.max(0, Math.min(1, value / max)) * 100 : 0;
  const barTone: Record<string, string> = {
    indigo: "bg-indigo-400",
    emerald: "bg-emerald-400",
    amber: "bg-amber-400",
    rose: "bg-rose-400",
    sky: "bg-sky-400",
  };
  return (
    <div
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-zinc-800", className)}
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={cn("h-full rounded-full transition-[width] duration-300", barTone[tone])}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
