import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type BadgeTone = "neutral" | "emerald" | "amber" | "rose" | "sky" | "indigo";

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: "border-zinc-700 bg-zinc-800/60 text-zinc-300",
  emerald: "border-emerald-800 bg-emerald-950 text-emerald-400",
  amber: "border-amber-800 bg-amber-950 text-amber-400",
  rose: "border-rose-800 bg-rose-950 text-rose-400",
  sky: "border-sky-800 bg-sky-950 text-sky-400",
  indigo: "border-indigo-800 bg-indigo-950 text-indigo-400",
};

export function Badge({
  children,
  tone = "neutral",
  mono = false,
  className,
}: {
  children: ReactNode;
  tone?: BadgeTone;
  mono?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] leading-none whitespace-nowrap",
        mono && "font-mono",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
