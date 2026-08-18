"use client";

import { useEffect, useMemo, useState } from "react";
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
import { formatAuditTimestamp, inventoryFor } from "@/lib/mock-data";
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

export default function AuditPage() {
  const { setFocus } = useCopilot();
  const { user } = useSession();
  const { facilityId, facility } = useFacility();
  const items = useMemo(() => inventoryFor(facilityId), [facilityId]);

  const [skuParam, setSkuParam] = useState<string | null>(null);
  useEffect(() => {
    setSkuParam(new URLSearchParams(window.location.search).get("sku"));
  }, []);
  const validSkuParam = skuParam && items.some((i) => i.id === skuParam) ? skuParam : null;

  const [itemId, setItemId] = useState(validSkuParam ?? items[0]?.id);

  useEffect(() => {
    if (validSkuParam) setItemId(validSkuParam);
  }, [validSkuParam]);

  const item = items.find((i) => i.id === itemId) ?? items[0];
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
      label: item.drugName,
      detail: `Audit trail · ${entries.length} logged events`,
      itemId: item.id,
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
            <CertificationBadge result={item.ndc ? certification[item.ndc] : { status: "unknown", reasons: 0 }} />
            {can(user?.role, "exportAudit") && (
              <Button
                variant="outline"
                size="sm"
                className="h-8 gap-1.5 text-xs"
                onClick={() => toast.success(`Audit trail for ${item.drugName} exported to compliance archive.`)}
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
            Newest first · rows written by the database trigger on <span className="font-mono">review_decision</span>.
            Export still archives locally until D3.
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
