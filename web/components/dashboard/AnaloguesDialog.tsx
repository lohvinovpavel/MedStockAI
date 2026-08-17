"use client";

import { Database, MapPin } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { StatusBadge } from "@/components/dashboard/StatusBadge";
import { useFacility } from "@/lib/facility-context";
import { facilities, type AnalogueEquivalence, type InventoryItem } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

const EQUIVALENCE_LABEL: Record<AnalogueEquivalence, string> = {
  bioequivalent: "Bio-equivalent",
  therapeutic: "Therapeutic alt.",
  "same-class": "Same ATC class",
};

function scoreClass(score: number) {
  if (score >= 90) return "text-emerald-600 dark:text-emerald-400";
  if (score >= 70) return "text-amber-600 dark:text-amber-400";
  return "text-muted-foreground";
}

export function AnaloguesDialog({
  item,
  open,
  onOpenChange,
}: {
  item: InventoryItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { facilityId, facility } = useFacility();
  if (!item) return null;

  // Best matches first — the ranking is the point of the lookup.
  const ranked = [...item.analogues].sort((a, b) => b.matchScore - a.matchScore);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Bio-equivalent analogues — {item.drugName}</DialogTitle>
          <DialogDescription className="flex flex-wrap items-center gap-1.5 text-xs">
            <Database className="size-3.5" />
            Ranked by RxNorm / ATC similarity · {ranked.length} match{ranked.length === 1 ? "" : "es"} · availability shown for{" "}
            <span className="font-medium text-foreground">{facility.name}</span>
          </DialogDescription>
        </DialogHeader>

        {ranked.length === 0 ? (
          <div className="flex flex-col items-center gap-1.5 rounded-md border border-dashed py-10 text-center">
            <Database className="size-6 text-muted-foreground/40" />
            <p className="text-xs font-medium">No analogues returned for this SKU</p>
            <p className="text-[11px] text-muted-foreground">
              No RxNorm or ATC equivalents are registered against {item.inn}.
            </p>
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {ranked.map((a) => {
              const localStock = a.stockByFacility[facilityId] ?? 0;
              // Closest site that actually has it, for when we don't.
              // distanceKm is measured from Central, so offset against the
              // active site rather than reporting Central as "0km away".
              const elsewhere = facilities
                .filter((f) => f.id !== facilityId && (a.stockByFacility[f.id] ?? 0) > 0)
                .map((f) => ({ ...f, awayKm: Math.abs(f.distanceKm - facility.distanceKm) }))
                .sort((x, y) => x.awayKm - y.awayKm)[0];

              return (
                <li key={a.id} className="flex items-start justify-between gap-3 rounded-md border p-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <span className={cn("shrink-0 font-mono text-sm font-semibold tabular-nums", scoreClass(a.matchScore))}>
                      {a.matchScore}%
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{a.drugName}</p>
                      <p className="truncate text-xs text-muted-foreground">{a.inn}</p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        <Badge variant="secondary" className="text-[10px] font-normal">
                          {EQUIVALENCE_LABEL[a.equivalence]}
                        </Badge>
                        <Badge variant="outline" className="text-[10px] font-normal">
                          {a.source}
                        </Badge>
                        <span className="font-mono text-[10px] text-muted-foreground">RxCUI {a.rxcui}</span>
                      </div>
                    </div>
                  </div>

                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <StatusBadge tone={localStock > 0 ? "normal" : "critical"}>
                      {localStock > 0 ? `${localStock} ${a.unit} here` : "Not stocked here"}
                    </StatusBadge>
                    {localStock === 0 && elsewhere && (
                      <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                        <MapPin className="size-3" />
                        {elsewhere.name} ({elsewhere.awayKm}km)
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}
