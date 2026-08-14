"use client";

import { useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, ArrowRight, Building2, MapPin, ShieldCheck, Truck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { useCopilot } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { facilityById, shortageAlerts, shortageMatrix, type FacilityStockRow } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

function coverageTone(row: FacilityStockRow): StatusTone {
  if (row.units === 0) return "stockout";
  if (row.daysOfSupply <= 5) return "critical";
  if (row.daysOfSupply >= 60) return "surplus";
  return "normal";
}

export default function ShortagesPage() {
  const { setFocus } = useCopilot();
  const { facilityId, facility } = useFacility();
  const [alertId, setAlertId] = useState(shortageAlerts[0].id);
  const [search, setSearch] = useState("");
  const [transferFrom, setTransferFrom] = useState<string | undefined>();
  const [transferQty, setTransferQty] = useState(30);
  const [dispatch, setDispatch] = useState<{ ref: string; time: string } | null>(null);

  const alert = shortageAlerts.find((a) => a.id === alertId)!;
  // Resolve each row against the facility registry so names, types and
  // distances have one source of truth, and "this facility" follows the
  // site currently selected in the sidebar.
  // distanceKm is measured from Central, so offset against the active site
  // rather than reporting Central as "0km away" from a clinic.
  const rows = (shortageMatrix[alertId] ?? []).map((r) => {
    const f = facilityById(r.facilityId);
    return {
      ...r,
      facility: f,
      awayKm: Math.abs(f.distanceKm - facility.distanceKm),
      isCurrent: r.facilityId === facilityId,
    };
  });
  const surplusFacilities = rows.filter((r) => coverageTone(r) === "surplus" && !r.isCurrent);
  const filteredRows = rows.filter((r) => r.facility.name.toLowerCase().includes(search.trim().toLowerCase()));

  function selectAlert(id: string) {
    setAlertId(id);
    setDispatch(null);
    setTransferFrom(undefined);
    const a = shortageAlerts.find((x) => x.id === id)!;
    setFocus({ kind: "alert", label: a.drugName, detail: `${a.source} shortage · ${a.severity} · ${a.note}` });
  }

  function requestTransfer() {
    if (!transferFrom) return;
    const ref = `TR-${Math.floor(1000 + Math.random() * 9000)}`;
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setDispatch({ ref, time });
    toast.success(`Transfer ${ref} dispatched to logistics.`);
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Shortage & Regional Matrix</h1>
        <p className="text-xs text-muted-foreground">National shortage alerts and cross-facility stock redistribution.</p>
      </div>

      <div className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950">
        <div className="flex items-center gap-2 text-xs font-medium text-amber-800 dark:text-amber-300">
          <AlertTriangle className="size-4" />
          {shortageAlerts.length} active shortage alerts from FDA / EMA drug shortage databases
        </div>
        <div className="flex flex-wrap gap-2">
          {shortageAlerts.map((a) => (
            <button
              key={a.id}
              onClick={() => selectAlert(a.id)}
              className={cn(
                "flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors",
                a.id === alertId ? "border-amber-400 bg-white dark:bg-amber-900/40" : "border-transparent bg-white/60 hover:bg-white dark:bg-amber-900/10 dark:hover:bg-amber-900/30",
              )}
            >
              <Badge variant={a.severity === "critical" ? "destructive" : "secondary"} className="text-[10px]">{a.source}</Badge>
              <span className="font-medium">{a.drugName}</span>
              <span className="text-muted-foreground">— {a.note}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[60%_40%]">
        <Card className="gap-3 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">Facility network — {alert.drugName}</CardTitle>
            <CardDescription className="text-xs">Live stock across regional hospitals, clinics, and pharmacies.</CardDescription>
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter facilities…"
              className="mt-2 h-8 text-xs"
            />
          </CardHeader>
          <CardContent className="px-4">
            <ul className="flex flex-col gap-2">
              {filteredRows.map((row) => {
                const tone = coverageTone(row);
                return (
                  <li
                    key={row.id}
                    className={cn(
                      "flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-xs",
                      row.isCurrent && "border-primary/40 bg-muted/40",
                    )}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <Building2 className="size-3.5 shrink-0 text-muted-foreground" />
                      <div className="min-w-0">
                        <p className="truncate font-medium">
                          {row.facility.name}
                          {row.isCurrent && <span className="ml-1 font-normal text-muted-foreground">(this facility)</span>}
                        </p>
                        <p className="flex items-center gap-1 text-muted-foreground">
                          <MapPin className="size-3" /> {row.facility.type} ·{" "}
                          {row.isCurrent ? "current site" : `${row.awayKm}km away`}
                        </p>
                      </div>
                    </div>
                    <StatusBadge tone={tone} className="shrink-0">
                      {row.units === 0 ? "Stockout" : `${row.units} units · ${row.daysOfSupply}d`}
                    </StatusBadge>
                  </li>
                );
              })}
              {filteredRows.length === 0 && (
                <li className="flex flex-col items-center gap-1.5 py-8 text-center">
                  <Building2 className="size-6 text-muted-foreground/40" />
                  <p className="text-xs font-medium">No facilities match &ldquo;{search}&rdquo;</p>
                  <p className="text-[11px] text-muted-foreground">Clear the filter to see the full network.</p>
                </li>
              )}
            </ul>
          </CardContent>
        </Card>

        <Card className="gap-3 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">Inter-facility transfer request</CardTitle>
            <CardDescription className="text-xs">Redistribute stock from a surplus facility to this location.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 px-4 text-xs">
            {surplusFacilities.length === 0 ? (
              <p className="text-muted-foreground">No surplus facilities available for {alert.drugName} right now.</p>
            ) : (
              <>
                <div className="flex flex-col gap-1.5">
                  <span className="text-muted-foreground">Source facility (surplus &gt;60 days)</span>
                  <Select value={transferFrom} onValueChange={setTransferFrom}>
                    <SelectTrigger size="sm" className="h-8 w-full text-xs">
                      <SelectValue placeholder="Select a facility" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {surplusFacilities.map((f) => (
                          <SelectItem key={f.id} value={f.id}>{f.facility.name} — {f.units} units</SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex items-center gap-2">
                  <Building2 className="size-3.5 text-muted-foreground" />
                  <span>{transferFrom ? surplusFacilities.find((f) => f.id === transferFrom)?.facility.name : "Source"}</span>
                  <ArrowRight className="size-3.5 text-muted-foreground" />
                  <span>{facility.name} (this facility)</span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Quantity to transfer</span>
                  <Input
                    type="number"
                    min={1}
                    value={transferQty}
                    onChange={(e) => setTransferQty(Math.max(1, Number(e.target.value)))}
                    className="h-7 w-24 text-right font-mono text-xs tabular-nums"
                  />
                </div>

                <Button size="sm" className="h-8 text-xs" disabled={!transferFrom} onClick={requestTransfer}>
                  <Truck data-icon="inline-start" />
                  Request {transferQty} units transfer
                </Button>

                {dispatch && (
                  <div className="rounded-md border border-emerald-200 bg-emerald-50 p-2.5 text-xs dark:border-emerald-900 dark:bg-emerald-950">
                    <div className="flex items-center gap-1.5 font-medium text-emerald-700 dark:text-emerald-400">
                      <ShieldCheck className="size-3.5" />
                      Transfer {dispatch.ref} dispatched
                    </div>
                    <Separator className="my-1.5" />
                    <p className="text-muted-foreground">
                      Logged by Dr. Casey Park at {dispatch.time} · automated logistics notified · audit trail recorded per ISO-27001.
                    </p>
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
