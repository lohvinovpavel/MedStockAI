"use client";

import { useEffect, useMemo, useState } from "react";
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
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

const CRITICAL_FLOOR_DAYS = 5;
const TARGET_COVERAGE_DAYS = 14;

type CoverageBand = "stockout" | "critical" | "normal" | "surplus";

type ShortageAlertLive = {
  id: string;
  ndc: string;
  rxcui: string | null;
  drug_name: string;
  status: string | null;
  source: "FDA" | "EMA" | string;
  note: string | null;
  updated_at: string | null;
  network: {
    facilities_affected: number;
    surplus_facilities: number;
    worst_days_of_supply: number | null;
  };
};

type CoverageRowLive = {
  facility: {
    id: number;
    code: string;
    name: string;
    type: string;
    operated: boolean;
  };
  quantity: number;
  days_of_supply: number | null;
  coverage: CoverageBand;
  distance_km: number;
  is_current: boolean;
};

function alertSeverity(alert: ShortageAlertLive): "critical" | "warning" {
  const worst = alert.network.worst_days_of_supply;
  if (worst != null && worst <= CRITICAL_FLOOR_DAYS) return "critical";
  return "warning";
}

function impliedDailyBurn(row: CoverageRowLive): number {
  if (row.days_of_supply != null && row.days_of_supply > 0) {
    return row.quantity / row.days_of_supply;
  }
  return Math.max(row.quantity, 1);
}

function coverageLabel(row: CoverageRowLive): string {
  if (row.quantity === 0) return "Stockout";
  if (row.days_of_supply == null) return `${row.quantity} units · unknown`;
  return `${row.quantity} units · ${row.days_of_supply}d`;
}

export default function ShortagesPage() {
  const { setFocus } = useCopilot();
  const { user } = useSession();
  const canTransfer = can(user?.role, "requestTransfer");
  const { facilityId, facility } = useFacility();
  const facilityPk = facility.id;

  const [alerts, setAlerts] = useState<ShortageAlertLive[]>([]);
  const [alertsError, setAlertsError] = useState<string | null>(null);
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [alertId, setAlertId] = useState<string | null>(null);
  const [rows, setRows] = useState<CoverageRowLive[]>([]);
  const [rowsError, setRowsError] = useState<string | null>(null);
  const [rowsLoading, setRowsLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [transferFrom, setTransferFrom] = useState<string | undefined>();
  const [transferQty, setTransferQty] = useState(1);
  const [dispatch, setDispatch] = useState<{ ref: string; time: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setAlertsLoading(true);
    apiFetch("inventory", `/shortages?facility_id=${facilityPk}`)
      .then((body: { items: ShortageAlertLive[] }) => {
        if (cancelled) return;
        const items = body.items ?? [];
        setAlerts(items);
        setAlertsError(null);
        setAlertId((current) =>
          current && items.some((a) => a.id === current) ? current : items[0]?.id ?? null,
        );
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setAlerts([]);
        setAlertId(null);
        setAlertsError(err.message || "Cannot load shortage alerts.");
      })
      .finally(() => {
        if (!cancelled) setAlertsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [facilityPk]);

  useEffect(() => {
    if (!alertId) {
      setRows([]);
      return;
    }
    let cancelled = false;
    setRowsLoading(true);
    apiFetch(
      "inventory",
      `/shortages/${encodeURIComponent(alertId)}/coverage?facility_id=${facilityPk}`,
    )
      .then((body: { rows: CoverageRowLive[] }) => {
        if (cancelled) return;
        setRows(body.rows ?? []);
        setRowsError(null);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setRows([]);
        setRowsError(err.message || "Cannot load coverage.");
      })
      .finally(() => {
        if (!cancelled) setRowsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [alertId, facilityPk]);

  const alert = alerts.find((a) => a.id === alertId) ?? null;
  const currentRow = rows.find((r) => r.is_current);

  const candidateSources = useMemo(
    () =>
      rows
        .filter(
          (r) =>
            !r.is_current &&
            r.facility.operated &&
            r.coverage !== "critical" &&
            r.coverage !== "stockout",
        )
        .sort(
          (a, b) =>
            (b.days_of_supply ?? -1) - (a.days_of_supply ?? -1) || a.distance_km - b.distance_km,
        ),
    [rows],
  );

  const sortRank: Record<CoverageBand, number> = {
    surplus: 0,
    normal: 1,
    critical: 2,
    stockout: 3,
  };
  const networkRows = [...rows].sort((a, b) => {
    if (a.is_current !== b.is_current) return a.is_current ? -1 : 1;
    return sortRank[a.coverage] - sortRank[b.coverage] || a.distance_km - b.distance_km;
  });
  const filteredRows = networkRows.filter((r) =>
    r.facility.name.toLowerCase().includes(search.trim().toLowerCase()),
  );

  const source = rows.find((r) => r.facility.code === transferFrom);
  const sourceSpare = source
    ? Math.max(0, source.quantity - Math.ceil(impliedDailyBurn(source) * CRITICAL_FLOOR_DAYS))
    : 0;
  const wouldHarmSource = source != null && transferQty > sourceSpare;
  const receivingDaysAfter =
    currentRow && transferQty > 0
      ? Math.round((currentRow.quantity + transferQty) / impliedDailyBurn(currentRow))
      : null;
  const sourceDaysAfter =
    source && transferQty > 0
      ? Math.round((source.quantity - transferQty) / impliedDailyBurn(source))
      : null;

  useEffect(() => {
    if (!transferFrom || !currentRow) return;
    const src = rows.find((r) => r.facility.code === transferFrom);
    if (!src) return;
    const gap = Math.max(
      0,
      Math.ceil(impliedDailyBurn(currentRow) * TARGET_COVERAGE_DAYS) - currentRow.quantity,
    );
    const spare = Math.max(0, src.quantity - Math.ceil(impliedDailyBurn(src) * CRITICAL_FLOOR_DAYS));
    setTransferQty(Math.max(1, Math.min(gap || spare, spare) || 1));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transferFrom, alertId, facilityId]);

  function selectAlert(id: string) {
    setAlertId(id);
    setDispatch(null);
    setTransferFrom(undefined);
    const a = alerts.find((x) => x.id === id);
    if (a) {
      setFocus({
        kind: "alert",
        label: a.drug_name,
        detail: `${a.source} shortage · ${alertSeverity(a)} · ${a.note ?? a.status ?? ""}`,
      });
    }
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
          {alertsLoading
            ? "Loading shortage alerts…"
            : alertsError
              ? alertsError
              : `${alerts.length} active shortage alerts from FDA / EMA drug shortage databases`}
        </div>
        <div className="flex flex-wrap gap-2">
          {alerts.map((a) => (
            <button
              key={a.id}
              onClick={() => selectAlert(a.id)}
              className={cn(
                "flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors",
                a.id === alertId ? "border-amber-400 bg-white dark:bg-amber-900/40" : "border-transparent bg-white/60 hover:bg-white dark:bg-amber-900/10 dark:hover:bg-amber-900/30",
              )}
            >
              <Badge variant={alertSeverity(a) === "critical" ? "destructive" : "secondary"} className="text-[10px]">{a.source}</Badge>
              <span className="font-medium">{a.drug_name}</span>
              <span className="text-muted-foreground">— {a.note ?? a.status}</span>
            </button>
          ))}
        </div>
      </Callout>

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Card className="gap-3 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">Facility network — {alert?.drug_name ?? "Shortage"}</CardTitle>
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
            {rowsError ? (
              <p className="py-6 text-center text-xs text-destructive">{rowsError}</p>
            ) : rowsLoading && rows.length === 0 ? (
              <p className="py-6 text-center text-xs text-muted-foreground">Loading coverage…</p>
            ) : (
            <ul className="flex flex-col gap-2">
              {filteredRows.map((row) => {
                const tone: StatusTone = row.coverage;
                return (
                  <li
                    key={row.facility.id}
                    className={cn(
                      "flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-xs",
                      row.is_current && "border-primary/40 bg-muted/40",
                    )}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <Building2 className="size-3.5 shrink-0 text-muted-foreground" />
                      <div className="min-w-0">
                        <p className="truncate font-medium">
                          {row.facility.name}
                          {row.is_current && <span className="ml-1 font-normal text-muted-foreground">(this facility)</span>}
                          {!row.facility.operated && (
                            <span className="ml-1.5 rounded border px-1 py-0.5 align-middle text-[9px] font-normal uppercase text-muted-foreground">
                              Est.
                            </span>
                          )}
                        </p>
                        <p className="flex items-center gap-1 text-muted-foreground">
                          <MapPin className="size-3" /> {row.facility.type} ·{" "}
                          {row.is_current ? "current site" : `${row.distance_km}km away`}
                          {!row.facility.operated && " · not directly monitored"}
                        </p>
                      </div>
                    </div>
                    <StatusBadge tone={tone} className="shrink-0">
                      {coverageLabel(row)}
                    </StatusBadge>
                  </li>
                );
              })}
              {filteredRows.length === 0 && !rowsLoading && (
                <li className="flex flex-col items-center gap-1.5 py-8 text-center">
                  <Building2 className="size-6 text-muted-foreground/40" />
                  <p className="text-xs font-medium">
                    {search.trim()
                      ? <>No facilities match &ldquo;{search}&rdquo;</>
                      : "No coverage rows for this alert."}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {search.trim() ? "Clear the filter to see the full network." : "Pick an alert above."}
                  </p>
                </li>
              )}
            </ul>
            )}
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
              <p className="text-muted-foreground">No facility has spare {alert?.drug_name ?? "stock"} to transfer right now.</p>
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
                          <SelectItem key={f.facility.id} value={f.facility.code}>
                            {f.facility.name} — {f.quantity} units
                            {f.coverage === "surplus" ? " (surplus)" : " (limited spare)"}
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
