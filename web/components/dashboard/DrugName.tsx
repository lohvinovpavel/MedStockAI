"use client";

import { parseDrugName } from "@/lib/drug-name";
import { cn } from "@/lib/utils";

// Name-first drug cell: ingredient (+ brand) as primary text, strength · form
// as muted detail. The full RxNorm string survives in the title attribute so
// hovering always shows the exact stored identity.
export function DrugName({
  name,
  fallback,
  className,
}: {
  name: string | null | undefined;
  fallback?: string;
  className?: string;
}) {
  if (!name) return <span className={cn("text-muted-foreground", className)}>{fallback ?? "—"}</span>;
  const parts = parseDrugName(name);
  return (
    <span className={cn("flex min-w-0 items-baseline gap-1.5", className)} title={name}>
      <span className="truncate font-medium">{parts.primary}</span>
      {parts.detail && (
        <span className="min-w-0 shrink-[2] truncate text-[11px] font-normal text-muted-foreground">
          {parts.detail}
        </span>
      )}
    </span>
  );
}
