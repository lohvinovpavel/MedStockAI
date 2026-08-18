import { cn } from "@/lib/utils";

// Industrial-terminal status chip: dark LED-panel look (works the same in
// light/dark app theme since it's a fixed dark chip), used anywhere a
// critical/warning/normal/stockout/surplus signal needs to read at a glance.
// `neutral` is for "we do not know", which is not a mild version of any of the
// others — a certification we could not check must never borrow the colour of
// one that came back clean.
export type StatusTone = "critical" | "warning" | "normal" | "stockout" | "surplus" | "neutral";

// Exported so other tone-coded UI (banners, KPI tiles) can share the same
// critical/warning/normal color mapping instead of each page re-deriving it
// with its own dark-mode convention. See Callout for the banner use.
export const TONE_STYLE: Record<StatusTone, string> = {
  critical: "border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/15 dark:text-red-300",
  stockout: "border-red-300 bg-red-100 text-red-800 dark:border-red-500/40 dark:bg-red-500/20 dark:text-red-200",
  warning: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-300",
  normal: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/15 dark:text-emerald-300",
  surplus: "border-[#e1e9f0] bg-[#f5f3ff] text-[#0f77ff] dark:border-sky-500/30 dark:bg-sky-500/15 dark:text-sky-300",
  neutral: "border-border bg-muted text-muted-foreground",
};

const DOT_STYLE: Record<StatusTone, string> = {
  critical: "bg-red-500 animate-pulse",
  stockout: "bg-red-500 animate-pulse",
  warning: "bg-amber-500",
  normal: "bg-emerald-500",
  surplus: "bg-[#0f77ff]",
  neutral: "bg-muted-foreground/40",
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
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 font-sans text-[11px] font-medium tracking-[0.004em]",
        TONE_STYLE[tone],
        className,
      )}
    >
      <span className={cn("size-1.5 shrink-0 rounded-full", DOT_STYLE[tone])} />
      {children}
    </span>
  );
}
