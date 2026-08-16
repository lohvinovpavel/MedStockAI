import type { LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const TILE_TONE: Record<"critical" | "warning" | "info", string> = {
  critical: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400",
  warning: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  info: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-400",
};

// Shared KPI tile (icon chip + value + label [+ hint]). Inventory and
// Purchase & Orders each had their own copy of this with slightly different
// dark-mode shades for the same tone — one component, one tone map.
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
    <Card className="gap-1 py-3">
      <CardContent className="flex items-center gap-3 px-4">
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-md",
            tone ? TILE_TONE[tone] : "bg-muted text-muted-foreground",
          )}
        >
          <Icon className="size-4" />
        </span>
        <div className="min-w-0">
          <p className="font-mono text-lg font-semibold leading-none tabular-nums">{value}</p>
          <p className="mt-1 text-xs text-muted-foreground">{label}</p>
          {hint && <p className="truncate text-[10px] text-muted-foreground/70">{hint}</p>}
        </div>
      </CardContent>
    </Card>
  );
}
