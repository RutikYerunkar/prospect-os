import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export function Tabs({ children }: { children: ReactNode }) {
  return (
    <div role="tablist" className="flex items-center gap-1 border-b border-zinc-800 px-2">
      {children}
    </div>
  );
}

export function Tab({
  active,
  onClick,
  children,
  disabled,
}: {
  active: boolean;
  onClick?: () => void;
  children: ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      role="tab"
      aria-selected={active}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "-mb-px border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400",
        active
          ? "border-indigo-400 text-zinc-100"
          : "border-transparent text-zinc-500 hover:text-zinc-300",
        disabled && "cursor-not-allowed opacity-50 hover:text-zinc-500",
      )}
    >
      {children}
    </button>
  );
}
