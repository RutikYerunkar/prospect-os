import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** A titled section container — heavier than Card, used for major page regions. */
export function Panel({
  title,
  action,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cn("rounded-md border border-zinc-800 bg-zinc-900", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-2.5">
          {typeof title === "string" ? (
            <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-400">
              {title}
            </h2>
          ) : (
            title
          )}
          {action}
        </header>
      )}
      <div className={cn(bodyClassName)}>{children}</div>
    </section>
  );
}
