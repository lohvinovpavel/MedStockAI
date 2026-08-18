"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, ArrowRight, Building2, MapPin, ShieldAlert, ShieldCheck, Truck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { Callout } from "@/components/dashboard/Callout";
import { useCopilot } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { useSession } from "@/lib/session";
import { can } from "@/lib/rbac";
import { facilityById, shortageAlerts, shortageRowsFor, type FacilityStockRow } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

// Below this, a facility is treated as needing the drug itself and can't be
// asked to give any up — matches the "Critical" band used everywhere else
// (inventory, forecasts). Above the target, it has room to spare.
const CRITICAL_FLOOR_DAYS = 5;
const TARGET_COVERAGE_DAYS = 14;

function coverageTone(row: FacilityStockRow): StatusTone {
  if (row.units === 0) return "stockout";
  if (row.daysOfSupply <= CRITICAL_FLOOR_DAYS) return "critical";
  if (row.daysOfSupply >= 60) return "surplus";
  return "normal";
}

// FacilityStockRow only carries units and days-of-supply (no burn rate of
// its own), so back one out for the transfer math below. Rows with
// daysOfSupply === 0 (stockout) fall back to treating the whole balance as
// one day's burn rather than dividing by zero.
function impliedDailyBurn(row: FacilityStockRow): number {
  return row.daysOfSupply > 0 ? row.units / row.daysOfSupply : Math.max(row.units, 1);
}

export default function ShortagesPage() {
  const { setFocus } = useCopilot();
  const { user } = useSession();
  const canTransfer = can(user?.role, "requestTransfer");
  const { facilityId, facility } = useFacility();
  const [alertId, setAlertId] = useState(shortageAlerts[0].id);
  const [search, setSearch] = useState("");
  const [transferFrom, setTransferFrom] = useState<string | undefined>();
  const [transferQty, setTransferQty] = useState(1);
  const [dispatch, setDispatch] = useState<{ ref: string; time: string } | null>(null);

  const alert = shortageAlerts.find((a) => a.id === alertId)!;
  // Derived from inventoryFor() for every operated facility — this can no
  // longer disagree with what Inventory shows for the same SKU. Resolve
  // each row against the facility registry so names, types and distances
  // have one source of truth, and "this facility" follows the site
  // currently selected in the sidebar.
  // distanceKm is measured from Central, so offset against the active site
  // rather than reporting Central as "0km away" from a clinic.
  const rows = shortageRowsFor(alertId).map((r) => {
    const f = facilityById(r.facilityId);
    return {
      ...r,
      facility: f,
      awayKm: Math.abs(f.distanceKm - facility.distanceKm),
      isCurrent: r.facilityId === facilityId,
    };
  });
  const currentRow = rows.find((r) => r.isCurrent);

  // Ranked, not filtered to a fixed ">60 days" gate — a facility with 45
  // days of supply is still a legitimate source, it just has less room
  // than one with 68. Anything at or below its own critical floor is
  // excluded outright: it needs the drug itself.
  const candidateSources = rows
    .filter((r) => !r.isCurrent && coverageTone(r) !== "critical" && coverageTone(r) !== "stockout")
    .sort((a, b) => b.daysOfSupply - a.daysOfSupply || a.awayKm - b.awayKm);

  // Best coverage first, worst last. `neutral` cannot come out of
  // coverageTone() — the key exists only to satisfy the Record — so it sorts
  // last rather than being given a coverage rank it has not earned.
  const sortRank: Record<StatusTone, number> = { surplus: 0, normal: 1, warning: 2, critical: 3, stockout: 4, neutral: 5 };
  const networkRows = [...rows].sort((a, b) => {
    if (a.isCurrent !== b.isCurrent) return a.isCurrent ? -1 : 1;
    return sortRank[coverageTone(a)] - sortRank[coverageTone(b)];
  });
  const filteredRows = networkRows.filter((r) => r.facility.name.toLowerCase().includes(search.trim().toLowerCase()));

  const source = rows.find((r) => r.facilityId === transferFrom);
  const sourceSpare = source ? Math.max(0, source.units - Math.ceil(impliedDailyBurn(source) * CRITICAL_FLOOR_DAYS)) : 0;
  const wouldHarmSource = source != null && transferQty > sourceSpare;
  const receivingDaysAfter =
    currentRow && transferQty > 0 ? Math.round((currentRow.units + transferQty) / impliedDailyBurn(currentRow)) : null;
  const sourceDaysAfter = source && transferQty > 0 ? Math.round((source.units - transferQty) / impliedDailyBurn(source)) : null;

  // Suggest the quantity that closes the gap to a 14-day target at this
  // facility, capped at what the chosen source can spare without dropping
  // below its own critical floor — rather than a flat, unexplained 30.
  useEffect(() => {
    if (!transferFrom || !currentRow) return;
    const src = rows.find((r) => r.facilityId === transferFrom);
    if (!src) return;
    const gap = Math.max(0, Math.ceil(impliedDailyBurn(currentRow) * TARGET_COVERAGE_DAYS) - currentRow.units);
    const spare = Math.max(0, src.units - Math.ceil(impliedDailyBurn(src) * CRITICAL_FLOOR_DAYS));
    setTransferQty(Math.max(1, Math.min(gap || spare, spare) || 1));
    // Only re-derive when the source or the active alert/facility changes —
    // not on every keystroke of a manual override.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transferFrom, alertId, facilityId]);

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

      <Callout tone="warning" className="flex flex-col gap-2 rounded-lg p-3">
        <div className="flex items-center gap-2 font-medium">
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
      </Callout>

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Card className="gap-3 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">Facility network — {alert.drugName}</CardTitle>
            <CardDescription className="text-xs">
              Live stock across regional hospitals, clinics, and pharmacies · sorted by ability to help.
            </CardDescription>
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
                          {!row.measured && (
                            <span className="ml-1.5 rounded border px-1 py-0.5 align-middle text-[9px] font-normal uppercase text-muted-foreground">
                              Est.
                            </span>
                          )}
                        </p>
                        <p className="flex items-center gap-1 text-muted-foreground">
                          <MapPin className="size-3" /> {row.facility.type} ·{" "}
                          {row.isCurrent ? "current site" : `${row.awayKm}km away`}
                          {!row.measured && " · not directly monitored"}
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
            <CardDescription className="text-xs">Redistribute stock from a facility with spare supply to this location.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 px-4 text-xs">
            {!canTransfer ? (
              <p className="text-muted-foreground">Read-only — requesting a transfer is a pharmacist/procurement action.</p>
            ) : candidateSources.length === 0 ? (
              <p className="text-muted-foreground">No facility has spare {alert.drugName} to transfer right now.</p>
            ) : (
              <>
                <div className="flex flex-col gap-1.5">
                  <span className="text-muted-foreground">Source facility</span>
                  <Select value={transferFrom} onValueChange={setTransferFrom}>
                    <SelectTrigger size="sm" className="h-8 w-full text-xs">
                      <SelectValue placeholder="Select a facility" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {candidateSources.map((f) => (
                          <SelectItem key={f.id} value={f.facilityId}>
                            {f.facility.name} — {f.units} units
                            {coverageTone(f) === "surplus" ? " (surplus)" : " (limited spare)"}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex items-center gap-2">
                  <Building2 className="size-3.5 text-muted-foreground" />
                  <span>{source ? source.facility.name : "Source"}</span>
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

                {source && receivingDaysAfter != null && sourceDaysAfter != null && (
                  <p className="text-muted-foreground">
                    Brings {facility.name} to <span className="font-mono tabular-nums text-foreground">{receivingDaysAfter}d</span>,
                    leaves {source.facility.name} at{" "}
                    <span className={cn("font-mono tabular-nums", wouldHarmSource ? "font-semibold text-red-600 dark:text-red-400" : "text-foreground")}>
                      {sourceDaysAfter}d
                    </span>.
                  </p>
                )}

                {wouldHarmSource && (
                  <Callout tone="critical" className="flex items-start gap-2">
                    <ShieldAlert className="size-3.5 shrink-0" />
                    <span>
                      This would leave {source?.facility.name} below its own {CRITICAL_FLOOR_DAYS}-day critical floor. Reduce the
                      quantity or pick another source before dispatching.
                    </span>
                  </Callout>
                )}

                <Button size="sm" className="h-8 text-xs" disabled={!transferFrom} onClick={requestTransfer}>
                  <Truck data-icon="inline-start" />
                  Request {transferQty} units transfer
                </Button>

                {dispatch && (
                  <Callout tone="normal">
                    <div className="flex items-center gap-1.5 font-medium">
                      <ShieldCheck className="size-3.5" />
                      Transfer {dispatch.ref} dispatched
                    </div>
                    <Separator className="my-1.5" />
                    <p className="text-muted-foreground">
                      Logged by {user?.full_name ?? "you"} at {dispatch.time} · automated logistics notified · audit trail recorded per ISO-27001.
                    </p>
                  </Callout>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
