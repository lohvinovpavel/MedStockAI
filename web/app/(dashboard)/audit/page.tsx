"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Bot, Download, ScrollText, Server, ShieldCheck, Stethoscope } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { useCopilot } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { auditLogFor, formatAuditTimestamp, inventoryFor, type AuditActorType, type InventoryItem } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

const CERT_TONE: Record<InventoryItem["certStatus"], StatusTone> = {
  valid: "normal",
  pending: "warning",
  expired: "critical",
};

const ACTOR_STYLE: Record<AuditActorType, { icon: typeof Bot; className: string }> = {
  clinician: { icon: Stethoscope, className: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-400" },
  ai: { icon: Bot, className: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-400" },
  system: { icon: Server, className: "bg-slate-100 text-slate-700 dark:bg-slate-500/15 dark:text-slate-400" },
  regulator: { icon: ShieldCheck, className: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400" },
};

export default function AuditPage() {
  const { setFocus } = useCopilot();
  const { facilityId, facility } = useFacility();
  const items = useMemo(() => inventoryFor(facilityId), [facilityId]);

  // Read ?sku= without useSearchParams(): that hook forces a Suspense
  // boundary that never resumes on a direct load/refresh of this
  // already-"use client" route (mainLen stayed 0 indefinitely). A plain
  // location.search read in an effect avoids the SSR bailout entirely.
  const [skuParam, setSkuParam] = useState<string | null>(null);
  useEffect(() => {
    setSkuParam(new URLSearchParams(window.location.search).get("sku"));
  }, []);
  const validSkuParam = skuParam && items.some((i) => i.id === skuParam) ? skuParam : null;

  const [itemId, setItemId] = useState(validSkuParam ?? items[0].id);

  // A row's "Audit Log" action deep-links with ?sku=; keep the picker in
  // sync if that changes (e.g. navigating here again for a different SKU).
  useEffect(() => {
    if (validSkuParam) setItemId(validSkuParam);
  }, [validSkuParam]);

  // Falls back to the first SKU when the selected one isn't stocked at the
  // facility you just switched to, rather than blowing up on a stale id.
  const item = items.find((i) => i.id === itemId) ?? items[0];
  const entries = useMemo(
    () => [...auditLogFor(item.id)].sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1)),
    [item.id],
  );

  useEffect(() => {
    setFocus({ kind: "sku", label: item.drugName, detail: `Audit trail · ${entries.length} logged events`, itemId: item.id });
  }, [item, entries.length, setFocus]);

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Audit Log & Compliance</h1>
          <p className="text-xs text-muted-foreground">
            Clinical, AI, and regulatory events for SKUs stocked at{" "}
            <span className="font-medium text-foreground">{facility.name}</span>.
          </p>
        </div>
        <Select value={item.id} onValueChange={setItemId}>
          <SelectTrigger size="sm" className="h-8 w-64 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {items.map((i) => (
                <SelectItem key={i.id} value={i.id}>{i.drugName}</SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </div>

      <Card className="gap-3 py-4">
        <CardContent className="flex flex-wrap items-center justify-between gap-3 px-4">
          <div>
            <p className="text-sm font-medium">{item.drugName}</p>
            <p className="font-mono text-xs tabular-nums text-muted-foreground">
              Batch {item.batchNumber} · {item.currentStock} {item.unit} on hand
            </p>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge tone={CERT_TONE[item.certStatus]} className="capitalize">
              {item.certAuthority} · {item.certStatus}
            </StatusBadge>
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 text-xs"
              onClick={() => toast.success(`Audit trail for ${item.drugName} exported to compliance archive.`)}
            >
              <Download data-icon="inline-start" />
              Export Audit Trail
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="gap-3 py-4">
        <CardHeader className="px-4">
          <CardTitle className="flex items-center gap-1.5 text-sm">
            <ScrollText className="size-4 text-muted-foreground" />
            Event history
          </CardTitle>
          <CardDescription className="text-xs">Newest first · clinician, AI pipeline, and regulator events.</CardDescription>
        </CardHeader>
        <CardContent className="px-4">
          {entries.length === 0 ? (
            <p className="py-8 text-center text-xs text-muted-foreground">No audit events recorded for this SKU yet.</p>
          ) : (
            <ol className="flex flex-col">
              {entries.map((entry, i) => {
                const style = ACTOR_STYLE[entry.actorType];
                const Icon = style.icon;
                return (
                  <li key={entry.id} className="relative flex gap-3 pb-6 last:pb-0">
                    {i < entries.length - 1 && (
                      <span className="absolute left-[15px] top-8 h-[calc(100%-1.5rem)] w-px bg-border" aria-hidden />
                    )}
                    <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-full", style.className)}>
                      <Icon className="size-4" />
                    </span>
                    <div className="flex flex-col gap-0.5 pt-1">
                      <span className="font-mono text-[11px] tabular-nums text-muted-foreground">{formatAuditTimestamp(entry.timestamp)}</span>
                      <p className="text-sm">
                        <span className="font-medium">{entry.actor}</span> {entry.action}
                        {entry.refId && <span className="text-muted-foreground"> ({entry.refId})</span>}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
