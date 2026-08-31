import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost";

const VARIANT_CLASSES: Record<Variant, string> = {
  primary: "bg-indigo-500 text-white hover:bg-indigo-400 disabled:bg-indigo-900 disabled:text-indigo-400/60",
  secondary:
    "border border-zinc-700 bg-zinc-900 text-zinc-200 hover:bg-zinc-800 disabled:text-zinc-600 disabled:hover:bg-zinc-900",
  ghost: "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 disabled:text-zinc-700",
};

export function Button({
  variant = "primary",
  className,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      disabled={disabled}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium",
        "transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400",
        "disabled:cursor-not-allowed",
        VARIANT_CLASSES[variant],
        className,
      )}
      {...props}
    />
  );
}
