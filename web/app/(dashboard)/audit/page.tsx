"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Bot, Download, ScrollText, Server, Stethoscope } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useCopilot } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { useSession } from "@/lib/session";
import { can } from "@/lib/rbac";
import { apiFetch } from "@/lib/api";
import { formatAuditTimestamp } from "@/lib/dates";
import { CertificationBadge, useCertificationStatuses } from "@/components/CertificationBadge";
import { DecisionTrail } from "@/components/dashboard/DecisionTrail";
import { cn } from "@/lib/utils";

type AuditActorType = "clinician" | "ai" | "system";

type AuditRow = {
  id: number;
  entity_type: string;
  entity_id: string;
  action: string;
  actor_id: string | null;
  actor_system: string | null;
  ai_dedupe_key: string | null;
  occurred_at: string | null;
};

const ACTOR_STYLE: Record<AuditActorType, { icon: typeof Bot; className: string }> = {
  clinician: { icon: Stethoscope, className: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-400" },
  ai: { icon: Bot, className: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-400" },
  system: { icon: Server, className: "bg-slate-100 text-slate-700 dark:bg-slate-500/15 dark:text-slate-400" },
};

function actorType(row: AuditRow): AuditActorType {
  if (row.ai_dedupe_key || row.actor_system === "copilot") return "ai";
  if (row.actor_system) return "system";
  return "clinician";
}

function actorLabel(row: AuditRow): string {
  return row.actor_system || "Clinician";
}

type ShelfSku = {
  ndc: string;
  name: string | null;
  quantity: number;
  lot: string | null;
  rxcui?: string | null;
};

export default function AuditPage() {
  const { setFocus } = useCopilot();
  const { user } = useSession();
  const { facility } = useFacility();
  const [items, setItems] = useState<ShelfSku[]>([]);

  const [skuParam, setSkuParam] = useState<string | null>(null);
  useEffect(() => {
    setSkuParam(new URLSearchParams(window.location.search).get("sku"));
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiFetch("inventory", `/items?facility_id=${facility.id}&limit=200`)
      .then((body: { items: ShelfSku[] }) => {
        if (!cancelled) setItems(body.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, [facility.id]);

  const validSkuParam = skuParam && items.some((i) => i.ndc === skuParam) ? skuParam : null;
  const [itemId, setItemId] = useState<string | undefined>(undefined);

  useEffect(() => {
    setItemId(validSkuParam ?? items[0]?.ndc);
  }, [validSkuParam, items]);

  const item = items.find((i) => i.ndc === itemId) ?? items[0];
  const certification = useCertificationStatuses(item?.ndc ? [item.ndc] : []);

  const [entries, setEntries] = useState<AuditRow[]>([]);
  const [auditError, setAuditError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch("compliance", "/audit")
      .then((body: { items: AuditRow[] }) => {
        if (cancelled) return;
        setEntries(body.items ?? []);
        setAuditError(null);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setEntries([]);
        setAuditError(err.message || "Cannot load audit log.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!item) return;
    setFocus({
      kind: "sku",
      label: item.name ?? item.ndc,
      detail: `Audit trail · ${entries.length} logged events`,
      itemId: item.ndc,
      ndc: item.ndc,
      rxcui: item.rxcui ?? null,
    });
  }, [item, entries.length, setFocus]);

  if (!item) {
    return <p className="p-4 text-sm text-muted-foreground">No SKUs at this site.</p>;
  }

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
        <Select value={item.ndc} onValueChange={setItemId}>
          <SelectTrigger size="sm" className="h-8 w-64 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {items.map((i) => (
                <SelectItem key={i.ndc} value={i.ndc}>{i.name ?? i.ndc}</SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </div>

      <Card className="gap-3 py-4">
        <CardContent className="flex flex-wrap items-center justify-between gap-3 px-4">
          <div>
            <p className="text-sm font-medium">{item.name ?? item.ndc}</p>
            <p className="font-mono text-xs tabular-nums text-muted-foreground">
              Lot {item.lot ?? "—"} · {item.quantity} on hand
            </p>
          </div>
          <div className="flex items-center gap-2">
            <CertificationBadge result={item.ndc ? certification[item.ndc] : { status: "unknown", reasons: 0 }} />
            {can(user?.role, "exportAudit") && (
              <Button
                variant="outline"
                size="sm"
                className="h-8 gap-1.5 text-xs"
                onClick={() => {
                  void (async () => {
                    try {
                      const params = new URLSearchParams();
                      if (item.ndc) params.set("ndc", item.ndc);
                      params.set("facility_id", String(facility.id));
                      const res = await fetch(`/api/compliance/export/compliance.csv?${params}`, {
                        credentials: "include",
                      });
                      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                      const blob = await res.blob();
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = `compliance-${item.ndc}.csv`;
                      a.click();
                      URL.revokeObjectURL(url);
                      toast.success(`Exported compliance trail for ${item.name ?? item.ndc}.`);
                    } catch (err) {
                      toast.error(err instanceof Error ? err.message : "Export failed.");
                    }
                  })();
                }}
              >
                <Download data-icon="inline-start" />
                Export Audit Trail
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <DecisionTrail />

      <Card className="gap-3 py-4">
        <CardHeader className="px-4">
          <CardTitle className="flex items-center gap-1.5 text-sm">
            <ScrollText className="size-4 text-muted-foreground" />
            Event history
          </CardTitle>
          <CardDescription className="text-xs">
            Newest first · rows written by the database trigger on <span className="font-mono">review_decision</span>,
            purchase orders, and transfers. Export is a streamed CSV (D3).
          </CardDescription>
        </CardHeader>
        <CardContent className="px-4">
          {auditError ? (
            <p className="py-8 text-center text-xs text-destructive">{auditError}</p>
          ) : entries.length === 0 ? (
            <p className="py-8 text-center text-xs text-muted-foreground">
              No audited events yet. A row appears when a recommendation is approved or rejected.
            </p>
          ) : (
            <ol className="flex flex-col">
              {entries.map((entry, i) => {
                const style = ACTOR_STYLE[actorType(entry)];
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
                      <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                        {entry.occurred_at ? formatAuditTimestamp(entry.occurred_at) : "—"}
                      </span>
                      <p className="text-sm">
                        <span className="font-medium">{actorLabel(entry)}</span>{" "}
                        {entry.action.toLowerCase()} {entry.entity_type.replaceAll("_", " ")}
                        <span className="text-muted-foreground"> (#{entry.entity_id})</span>
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
