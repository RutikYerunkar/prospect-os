import type { ReactNode, TdHTMLAttributes, ThHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full min-w-[720px] border-collapse text-left text-sm", className)}>
        {children}
      </table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return <thead className="border-b border-zinc-800">{children}</thead>;
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody className="divide-y divide-zinc-800/70">{children}</tbody>;
}

export function TR({
  children,
  onClick,
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  return (
    <tr
      onClick={onClick}
      className={cn(
        onClick && "cursor-pointer transition-colors hover:bg-zinc-800/50",
        className,
      )}
    >
      {children}
    </tr>
  );
}

export function TH({ children, className, ...rest }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-zinc-500",
        className,
      )}
      {...rest}
    >
      {children}
    </th>
  );
}

export function TD({ children, className, ...rest }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td className={cn("px-3 py-2.5 align-middle text-zinc-200", className)} {...rest}>
      {children}
    </td>
  );
}
