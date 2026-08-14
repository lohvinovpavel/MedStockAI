import { cn } from "@/lib/utils";

// Industrial-terminal status chip: dark LED-panel look (works the same in
// light/dark app theme since it's a fixed dark chip), used anywhere a
// critical/warning/normal/stockout/surplus signal needs to read at a glance.
export type StatusTone = "critical" | "warning" | "normal" | "stockout" | "surplus";

const TONE_STYLE: Record<StatusTone, string> = {
  critical: "border-red-200 bg-red-50 text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-400",
  stockout: "border-red-300 bg-red-100 text-red-800 dark:border-red-500/35 dark:bg-red-500/15 dark:text-red-300",
  warning: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-400",
  normal: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/25 dark:bg-emerald-500/10 dark:text-emerald-400",
  surplus: "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-500/25 dark:bg-sky-500/10 dark:text-sky-400",
};

const DOT_STYLE: Record<StatusTone, string> = {
  critical: "bg-red-500 animate-pulse",
  stockout: "bg-red-500 animate-pulse",
  warning: "bg-amber-500",
  normal: "bg-emerald-500",
  surplus: "bg-sky-500",
};

export function StatusBadge({
  tone,
  children,
  className,
}: {
  tone: StatusTone;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide",
        TONE_STYLE[tone],
        className,
      )}
    >
      <span className={cn("size-1.5 shrink-0 rounded-full", DOT_STYLE[tone])} />
      {children}
    </span>
  );
}
