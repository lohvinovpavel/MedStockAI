import type { LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const TILE_TONE: Record<"critical" | "warning" | "info", string> = {
  critical: "border border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/15 dark:text-red-300",
  warning: "border border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-300",
  info: "border border-[#e1e9f0] bg-[#f5f3ff] text-[#0f77ff] dark:border-sky-500/30 dark:bg-sky-500/15 dark:text-sky-300",
};

export function StatTile({
  icon: Icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  hint?: string;
  tone?: "critical" | "warning" | "info";
}) {
  return (
    <Card className="gap-1 py-3 border-border bg-card">
      <CardContent className="flex items-center gap-3 px-4">
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-lg",
            tone ? TILE_TONE[tone] : "border border-border bg-muted text-muted-foreground",
          )}
        >
          <Icon className="size-4" />
        </span>
        <div className="min-w-0">
          <p className="font-sans text-lg font-semibold leading-none text-foreground tabular-nums tracking-[0.008em]">{value}</p>
          <p className="mt-1 text-xs text-muted-foreground tracking-[0.004em]">{label}</p>
          {hint && <p className="truncate text-[10px] text-muted-foreground/80">{hint}</p>}
        </div>
      </CardContent>
    </Card>
  );
}
