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
import { useCopilot } from "@/lib/copilot-context";
import { shortageAlerts, shortageMatrix, type FacilityStockRow } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

function coverageTone(row: FacilityStockRow): "stockout" | "critical" | "surplus" | "normal" {
  if (row.units === 0) return "stockout";
  if (row.daysOfSupply <= 5) return "critical";
  if (row.daysOfSupply >= 60) return "surplus";
  return "normal";
}

const TONE_CLASS: Record<ReturnType<typeof coverageTone>, string> = {
  stockout: "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400",
  critical: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-400",
  surplus: "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-400",
  normal: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-400",
};

export default function ShortagesPage() {
  const { setFocus } = useCopilot();
  const [alertId, setAlertId] = useState(shortageAlerts[0].id);
  const [search, setSearch] = useState("");
  const [transferFrom, setTransferFrom] = useState<string | undefined>();
  const [transferQty, setTransferQty] = useState(30);
  const [dispatch, setDispatch] = useState<{ ref: string; time: string } | null>(null);

  const alert = shortageAlerts.find((a) => a.id === alertId)!;
  const rows = shortageMatrix[alertId] ?? [];
  const surplusFacilities = rows.filter((r) => coverageTone(r) === "surplus");
  const filteredRows = rows.filter((r) => r.facility.toLowerCase().includes(search.trim().toLowerCase()));

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
                  <li key={row.id} className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-xs">
                    <div className="flex min-w-0 items-center gap-2">
                      <Building2 className="size-3.5 shrink-0 text-muted-foreground" />
                      <div className="min-w-0">
                        <p className="truncate font-medium">{row.facility}</p>
                        <p className="flex items-center gap-1 text-muted-foreground">
                          <MapPin className="size-3" /> {row.type} · {row.distanceKm === 0 ? "this facility" : `${row.distanceKm}km away`}
                        </p>
                      </div>
                    </div>
                    <Badge variant="outline" className={cn("shrink-0 text-[11px]", TONE_CLASS[tone])}>
                      {row.units === 0 ? "Stockout" : `${row.units} units · ${row.daysOfSupply}d`}
                    </Badge>
                  </li>
                );
              })}
              {filteredRows.length === 0 && (
                <li className="py-6 text-center text-xs text-muted-foreground">No facilities match this filter.</li>
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
                          <SelectItem key={f.id} value={f.id}>{f.facility} — {f.units} units</SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex items-center gap-2">
                  <Building2 className="size-3.5 text-muted-foreground" />
                  <span>{transferFrom ? surplusFacilities.find((f) => f.id === transferFrom)?.facility : "Source"}</span>
                  <ArrowRight className="size-3.5 text-muted-foreground" />
                  <span>Central Hospital (this facility)</span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Quantity to transfer</span>
                  <Input
                    type="number"
                    min={1}
                    value={transferQty}
                    onChange={(e) => setTransferQty(Math.max(1, Number(e.target.value)))}
                    className="h-7 w-24 text-right text-xs"
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
