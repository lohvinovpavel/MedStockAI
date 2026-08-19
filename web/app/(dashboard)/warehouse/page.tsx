"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, XAxis, YAxis } from "recharts";
import { Building2, Refrigerator, ThermometerSun } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { StatusBadge } from "@/components/dashboard/StatusBadge";
import { Callout } from "@/components/dashboard/Callout";
import { SortableHead, compareValues, nextSortState, type SortState } from "@/components/dashboard/SortableHead";
import { DrugName } from "@/components/dashboard/DrugName";
import { apiFetch } from "@/lib/api";
import { useCopilot } from "@/lib/copilot-context";
import { parseDrugName } from "@/lib/drug-name";
import { cn } from "@/lib/utils";

// Mirrors the class-level requirements seeded on the drug table
// (data/demo/drugs.csv) — used only to draw requirement bands; the server's
// /excursions endpoint is the authority on violations.
const CLASS_RANGES: Record<string, { minC: number; maxC: number; maxRh: number }> = {
  refrigerated: { minC: 2, maxC: 8, maxRh: 75 },
  crt: { minC: 15, maxC: 25, maxRh: 60 },
  freezer: { minC: -25, maxC: -15, maxRh: 75 },
};

interface Facility { id: number; code: string; name: string; type: string; operated: boolean }
interface Location { id: number; code: string; name: string; kind: string }
interface StockItem { ndc: string; name: string | null; location: string; quantity: number; storage_class: string | null; drug_class: string | null }
interface ConsumptionPoint { date: string; qty: number; stockout: boolean }
interface ConditionPoint { ts: string; temperature_c: number; humidity_pct: number }
interface Excursion {
  facility: string; location: string; location_kind: string; ndc: string; drug: string;
  storage_class: string; required_min_c: number; required_max_c: number;
  required_max_humidity_pct: number; quantity: number; first_ts: string; last_ts: string;
  hours: number; observed_min_c: number; observed_max_c: number;
  observed_max_humidity_pct: number; violations: string[];
}

const consumptionConfig: ChartConfig = {
  qty: { label: "Units consumed", color: "var(--chart-2)" },
};
const temperatureConfig: ChartConfig = {
  temperature_c: { label: "Temperature °C", color: "var(--chart-1)" },
};
const humidityConfig: ChartConfig = {
  humidity_pct: { label: "Humidity %RH", color: "var(--chart-3)" },
};

const RANGES = [
  { value: "90d", label: "90 days · daily", days: 90, bucket: 1 },
  { value: "1y", label: "1 year · weekly", days: 365, bucket: 7 },
  { value: "3y", label: "3 years · weekly", days: 1096, bucket: 7 },
] as const;

function shortDate(iso: string, monthly: boolean) {
  const d = new Date(`${iso}T00:00:00Z`);
  return monthly
    ? d.toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" })
    : d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

// Sum daily points into fixed-size buckets; a bucket inherits a stockout mark
// if any of its days was censored, so shortage windows survive aggregation.
function bucketize(points: ConsumptionPoint[], size: number) {
  if (size <= 1) return points.map((p) => ({ ...p }));
  const out: ConsumptionPoint[] = [];
  for (let i = 0; i < points.length; i += size) {
    const slice = points.slice(i, i + size);
    out.push({
      date: slice[0].date,
      qty: slice.reduce((s, p) => s + p.qty, 0),
      stockout: slice.some((p) => p.stockout),
    });
  }
  return out;
}

type StockSortKey = "name" | "ndc" | "location" | "storage_class" | "drug_class" | "quantity";

function stockSortValue(item: StockItem, key: StockSortKey): string | number {
  switch (key) {
    case "name":
      return parseDrugName(item.name ?? item.ndc).primary.toLowerCase();
    case "ndc":
      return item.ndc;
    case "location":
      return item.location;
    case "storage_class":
      return item.storage_class ?? "";
    case "drug_class":
      return (item.drug_class ?? "").toLowerCase();
    case "quantity":
      return item.quantity;
  }
}

type ExcursionSortKey = "drug" | "quantity" | "hours";

function excursionSortValue(e: Excursion, key: ExcursionSortKey): string | number {
  switch (key) {
    case "drug":
      return parseDrugName(e.drug ?? e.ndc).primary.toLowerCase();
    case "quantity":
      return e.quantity;
    case "hours":
      return e.hours;
  }
}

// Contiguous stockout buckets → [start, end] spans for ReferenceArea shading.
function stockoutSpans(points: ConsumptionPoint[]) {
  const spans: { x1: string; x2: string }[] = [];
  let open: { x1: string; x2: string } | null = null;
  for (const p of points) {
    if (p.stockout) {
      if (open) open.x2 = p.date;
      else open = { x1: p.date, x2: p.date };
    } else if (open) {
      spans.push(open);
      open = null;
    }
  }
  if (open) spans.push(open);
  return spans;
}

export default function WarehousePage() {
  const { setFocus } = useCopilot();
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [facilityId, setFacilityId] = useState<number | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [stock, setStock] = useState<StockItem[]>([]);
  const [excursions, setExcursions] = useState<Excursion[]>([]);
  const [drugNdc, setDrugNdc] = useState<string | null>(null);
  const [range, setRange] = useState<(typeof RANGES)[number]["value"]>("3y");
  const [series, setSeries] = useState<ConsumptionPoint[]>([]);
  const [locationId, setLocationId] = useState<number | null>(null);
  const [conditions, setConditions] = useState<ConditionPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [stockSort, setStockSort] = useState<SortState<StockSortKey>>(null);
  const [stockQuery, setStockQuery] = useState("");
  const [classFilter, setClassFilter] = useState("all");
  const [storageFilter, setStorageFilter] = useState("all");
  // One sort state shared by every per-location excursion table — they are
  // small slices of the same shape, and one set of arrows is less surprising
  // than each group remembering its own.
  const [exSort, setExSort] = useState<SortState<ExcursionSortKey>>(null);

  const fail = useCallback((what: string) => (err: unknown) => {
    toast.error(`Failed to load ${what}`, { description: err instanceof Error ? err.message : undefined });
  }, []);

  useEffect(() => {
    apiFetch("warehouse", "/facilities?operated=true")
      .then((body) => {
        setFacilities(body.items);
        setFacilityId((prev) => prev ?? body.items[0]?.id ?? null);
      })
      .catch(fail("facilities"))
      .finally(() => setLoading(false));
  }, [fail]);

  useEffect(() => {
    if (facilityId == null) return;
    apiFetch("warehouse", `/locations?facility_id=${facilityId}`)
      .then((body) => {
        setLocations(body.items);
        setLocationId(body.items[0]?.id ?? null);
      })
      .catch(fail("locations"));
    apiFetch("warehouse", `/stock?facility_id=${facilityId}`)
      .then((body) => {
        setStock(body.items);
        setDrugNdc((prev) => (prev && body.items.some((i: StockItem) => i.ndc === prev) ? prev : body.items[0]?.ndc ?? null));
      })
      .catch(fail("stock"));
    apiFetch("warehouse", `/excursions?facility_id=${facilityId}`)
      .then((body) => setExcursions(body.items))
      .catch(fail("excursions"));
  }, [facilityId, fail]);

  const rangeSpec = RANGES.find((r) => r.value === range)!;
  // Full history in one fetch (~1100 points); the range control slices from
  // the series' own tail so the window is anchored to the data, not the
  // browser clock — the demo dataset ends on a fixed date.
  useEffect(() => {
    if (facilityId == null || drugNdc == null) return;
    apiFetch("warehouse", `/consumption?ndc=${encodeURIComponent(drugNdc)}&facility_id=${facilityId}`)
      .then((body) => setSeries(body.items))
      .catch(fail("consumption history"));
  }, [facilityId, drugNdc, fail]);

  useEffect(() => {
    if (locationId == null) return;
    apiFetch("warehouse", `/locations/${locationId}/conditions`)
      .then((body) => setConditions(body.items))
      .catch(fail("conditions"));
  }, [locationId, fail]);

  const facility = facilities.find((f) => f.id === facilityId) ?? null;
  const drugs = useMemo(() => {
    const seen = new Map<string, StockItem>();
    for (const item of stock) if (!seen.has(item.ndc)) seen.set(item.ndc, item);
    return [...seen.values()].sort((a, b) => (a.name ?? a.ndc).localeCompare(b.name ?? b.ndc));
  }, [stock]);

  const chartData = useMemo(
    () => bucketize(series.slice(-rangeSpec.days), rangeSpec.bucket),
    [series, rangeSpec.days, rangeSpec.bucket],
  );
  const spans = useMemo(() => stockoutSpans(chartData), [chartData]);
  const monthlyTicks = rangeSpec.days > 200;

  const location = locations.find((l) => l.id === locationId) ?? null;
  // Strictest requirement band across the classes actually stocked at the
  // selected location — the server's /excursions stays the authority.
  const band = useMemo(() => {
    if (!location) return null;
    const classes = [...new Set(stock.filter((s) => s.location === location.code && s.storage_class).map((s) => s.storage_class!))];
    if (classes.length === 0) return null;
    const ranges = classes.map((c) => CLASS_RANGES[c]).filter(Boolean);
    if (ranges.length === 0) return null;
    return {
      minC: Math.max(...ranges.map((r) => r.minC)),
      maxC: Math.min(...ranges.map((r) => r.maxC)),
      maxRh: Math.min(...ranges.map((r) => r.maxRh)),
    };
  }, [location, stock]);

  const conditionData = useMemo(
    () => conditions.map((p) => ({ ...p, label: p.ts.slice(5, 16).replace("T", " ") })),
    [conditions],
  );

  // One alert per location: the drug rows say which stock is affected.
  const excursionGroups = useMemo(() => {
    const groups = new Map<string, Excursion[]>();
    for (const e of excursions) {
      const key = e.location;
      groups.set(key, [...(groups.get(key) ?? []), e]);
    }
    if (exSort) {
      for (const [, group] of groups) {
        group.sort((a, b) => {
          const r = compareValues(excursionSortValue(a, exSort.key), excursionSortValue(b, exSort.key));
          return exSort.direction === "asc" ? r : -r;
        });
      }
    }
    return [...groups.entries()];
  }, [excursions, exSort]);

  // Keep the assistant's context in sync with the facility/location being
  // viewed here — otherwise a question asked in this page still answers
  // about whatever SKU or forecast was last focused elsewhere.
  useEffect(() => {
    if (!facility) return;
    const excursionCount = excursionGroups.length;
    setFocus({
      kind: "warehouse",
      label: facility.name,
      detail:
        excursionCount > 0
          ? `${excursionCount} storage location${excursionCount > 1 ? "s" : ""} with active excursions · ${stock.length} shelf positions`
          : `${stock.length} shelf positions · all monitored locations within range`,
      facilityId: facility.id,
      locationId,
    });
  }, [facility, excursionGroups, stock.length, locationId, setFocus]);

  const drugClasses = useMemo(
    () => [...new Set(stock.map((s) => s.drug_class).filter((c): c is string => !!c))].sort(),
    [stock],
  );
  const storageClasses = useMemo(
    () => [...new Set(stock.map((s) => s.storage_class).filter((c): c is string => !!c))].sort(),
    [stock],
  );
  // Sorting/filtering are client-side over the full facility list (~100 rows);
  // unsorted keeps the server's name order.
  const stockRows = useMemo(() => {
    const tokens = stockQuery.toLowerCase().split(/\s+/).filter(Boolean);
    const filtered = stock.filter((item) => {
      if (classFilter !== "all" && item.drug_class !== classFilter) return false;
      if (storageFilter !== "all" && item.storage_class !== storageFilter) return false;
      if (tokens.length === 0) return true;
      const hay = `${item.name ?? ""} ${parseDrugName(item.name ?? item.ndc).primary} ${item.ndc} ${item.location} ${item.drug_class ?? ""}`.toLowerCase();
      return tokens.every((t) => hay.includes(t));
    });
    if (!stockSort) return filtered;
    return [...filtered].sort((a, b) => {
      const r = compareValues(stockSortValue(a, stockSort.key), stockSortValue(b, stockSort.key));
      return stockSort.direction === "asc" ? r : -r;
    });
  }, [stock, stockQuery, classFilter, storageFilter, stockSort]);

  if (loading) {
    return (
      <div className="flex flex-col gap-4 p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex flex-col gap-1.5">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-3 w-72" />
          </div>
          <Skeleton className="h-8 w-64" />
        </div>
        <div className="grid gap-4 lg:grid-cols-[2fr_3fr]">
          <Card className="gap-2 py-4">
            <CardContent className="flex flex-col gap-2 px-4">
              {Array.from({ length: 5 }, (_, i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </CardContent>
          </Card>
          <Card className="gap-2 py-4">
            <CardContent className="px-4">
              <Skeleton className="h-64 w-full" />
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Warehouse</h1>
          <p className="text-xs text-muted-foreground">
            Facility structure, stock placement, consumption history and storage conditions.
          </p>
        </div>
        <Select value={facilityId != null ? String(facilityId) : undefined} onValueChange={(v) => setFacilityId(Number(v))}>
          <SelectTrigger size="sm" className="h-8 w-64 text-xs">
            <Building2 className="mr-1 size-3.5" />
            <SelectValue placeholder="Facility" />
          </SelectTrigger>
          <SelectContent>
            {facilities.map((f) => (
              <SelectItem key={f.id} value={String(f.id)} className="text-xs">
                {f.name} · {f.type}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {excursionGroups.length > 0 ? (
        <Callout tone="critical">
          <div className="mb-2 flex items-center gap-2 font-semibold">
            <ThermometerSun className="size-4" />
            {excursionGroups.length} storage location{excursionGroups.length > 1 ? "s" : ""} with condition excursions at {facility?.name}
          </div>
          <div className="flex flex-col gap-3">
            {excursionGroups.map(([locationCode, items]) => {
              const first = items[0];
              const worstHours = Math.max(...items.map((i) => i.hours));
              const kinds = [...new Set(items.flatMap((i) => i.violations))];
              return (
                <div key={locationCode} className="rounded-md border border-red-200/60 bg-background/60 p-2 text-foreground dark:border-red-500/20">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <span className="font-medium">{locationCode}</span>
                    <span className="text-muted-foreground">({first.location_kind.replace("_", " ")})</span>
                    {kinds.map((k) => (
                      <StatusBadge key={k} tone={k === "temperature" ? "critical" : "warning"}>{k}</StatusBadge>
                    ))}
                    <span className="ml-auto text-muted-foreground">
                      {first.first_ts.slice(0, 10)} → {first.last_ts.slice(0, 10)} · up to {worstHours}h out of range
                    </span>
                  </div>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <SortableHead sortKey="drug" sort={exSort} onSort={(k) => setExSort((s) => nextSortState(s, k))} className="text-xs">Affected drug</SortableHead>
                        <SortableHead sortKey="quantity" sort={exSort} onSort={(k) => setExSort((s) => nextSortState(s, k))} className="text-xs">On hand</SortableHead>
                        <TableHead className="text-xs">Required</TableHead>
                        <TableHead className="text-xs">Observed</TableHead>
                        <SortableHead sortKey="hours" sort={exSort} onSort={(k) => setExSort((s) => nextSortState(s, k))} className="text-xs">Hours</SortableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {items.map((e) => (
                        <TableRow key={e.ndc}>
                          <TableCell className="max-w-[26rem] text-xs">
                            <DrugName name={e.drug} fallback={e.ndc} />
                          </TableCell>
                          <TableCell className="text-xs tabular-nums">{e.quantity}</TableCell>
                          <TableCell className="text-xs tabular-nums">
                            {e.required_min_c}–{e.required_max_c} °C · ≤{e.required_max_humidity_pct}%
                          </TableCell>
                          <TableCell className="text-xs tabular-nums">
                            {e.violations.includes("temperature")
                              ? `${e.observed_min_c.toFixed(1)}–${e.observed_max_c.toFixed(1)} °C`
                              : `${e.observed_max_humidity_pct.toFixed(0)}%RH`}
                          </TableCell>
                          <TableCell className="text-xs tabular-nums">{e.hours}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              );
            })}
          </div>
        </Callout>
      ) : (
        <Callout tone="normal">All monitored storage locations at {facility?.name} are within their drugs&apos; requirements.</Callout>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle className="text-sm">Consumption history</CardTitle>
                <CardDescription className="text-xs">
                  Recorded daily usage at {facility?.name}. Red bands mark stockout windows — recorded zeros there are censored demand, not zero demand.
                </CardDescription>
              </div>
              <div className="flex items-center gap-2">
                {/* Selection moved to the Stock by location rows below; this
                    just names the drug the chart is showing. */}
                <DrugName
                  name={drugs.find((d) => d.ndc === drugNdc)?.name ?? drugNdc}
                  className="max-w-64 text-xs"
                />
                <Select value={range} onValueChange={(v) => setRange(v as typeof range)}>
                  <SelectTrigger size="sm" className="h-8 w-40 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RANGES.map((r) => (
                      <SelectItem key={r.value} value={r.value} className="text-xs">{r.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <ChartContainer config={consumptionConfig} className="h-64 w-full">
              <AreaChart data={chartData} margin={{ left: 4, right: 8, top: 8 }}>
                <CartesianGrid vertical={false} strokeOpacity={0.4} />
                <XAxis
                  dataKey="date"
                  tickLine={false}
                  axisLine={false}
                  minTickGap={48}
                  tickFormatter={(v: string) => shortDate(v, monthlyTicks)}
                  className="text-[10px]"
                />
                <YAxis tickLine={false} axisLine={false} width={40} className="text-[10px]" />
                <ChartTooltip content={<ChartTooltipContent labelFormatter={(v) => String(v)} />} />
                {spans.map((s) => (
                  <ReferenceArea key={s.x1} x1={s.x1} x2={s.x2} fill="var(--chart-5)" fillOpacity={0.12} strokeOpacity={0} />
                ))}
                <Area type="monotone" dataKey="qty" stroke="var(--color-qty)" strokeWidth={2} fill="var(--color-qty)" fillOpacity={0.12} dot={false} />
              </AreaChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle className="text-sm">Storage conditions</CardTitle>
                <CardDescription className="text-xs">
                  Hourly telemetry, last 90 days. Dashed lines are the strictest requirement of the drugs stored here.
                </CardDescription>
              </div>
              <Select value={locationId != null ? String(locationId) : undefined} onValueChange={(v) => setLocationId(Number(v))}>
                <SelectTrigger size="sm" className="h-8 w-52 text-xs">
                  <Refrigerator className="mr-1 size-3.5" />
                  <SelectValue placeholder="Location" />
                </SelectTrigger>
                <SelectContent>
                  {locations.map((l) => (
                    <SelectItem key={l.id} value={String(l.id)} className="text-xs">
                      {l.name} · {l.kind.replace("_", " ")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <ChartContainer config={temperatureConfig} className="h-28 w-full">
              <LineChart data={conditionData} margin={{ left: 4, right: 8, top: 4 }}>
                <CartesianGrid vertical={false} strokeOpacity={0.4} />
                <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={64} className="text-[10px]" />
                <YAxis tickLine={false} axisLine={false} width={40} domain={["auto", "auto"]} className="text-[10px]" />
                <ChartTooltip content={<ChartTooltipContent labelFormatter={(v) => String(v)} />} />
                {band && <ReferenceLine y={band.maxC} stroke="var(--chart-5)" strokeDasharray="4 4" strokeOpacity={0.7} />}
                {band && <ReferenceLine y={band.minC} stroke="var(--chart-5)" strokeDasharray="4 4" strokeOpacity={0.7} />}
                <Line type="monotone" dataKey="temperature_c" stroke="var(--color-temperature_c)" strokeWidth={1.5} dot={false} />
              </LineChart>
            </ChartContainer>
            <ChartContainer config={humidityConfig} className="h-28 w-full">
              <LineChart data={conditionData} margin={{ left: 4, right: 8, top: 4 }}>
                <CartesianGrid vertical={false} strokeOpacity={0.4} />
                <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={64} className="text-[10px]" />
                <YAxis tickLine={false} axisLine={false} width={40} domain={["auto", "auto"]} className="text-[10px]" />
                <ChartTooltip content={<ChartTooltipContent labelFormatter={(v) => String(v)} />} />
                {band && <ReferenceLine y={band.maxRh} stroke="var(--chart-5)" strokeDasharray="4 4" strokeOpacity={0.7} />}
                <Line type="monotone" dataKey="humidity_pct" stroke="var(--color-humidity_pct)" strokeWidth={1.5} dot={false} />
              </LineChart>
            </ChartContainer>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-end justify-between gap-2">
            <div>
              <CardTitle className="text-sm">Stock by location</CardTitle>
              <CardDescription className="text-xs">
                {stock.length} shelf positions at {facility?.name}. Click a row to chart its consumption history.
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Input
                value={stockQuery}
                onChange={(e) => setStockQuery(e.target.value)}
                placeholder="Search drug, NDC, location…"
                className="h-8 w-56 text-xs"
              />
              <Select value={classFilter} onValueChange={setClassFilter}>
                <SelectTrigger size="sm" className="h-8 w-52 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all" className="text-xs">All classes</SelectItem>
                  {drugClasses.map((c) => (
                    <SelectItem key={c} value={c} className="text-xs">{c}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={storageFilter} onValueChange={setStorageFilter}>
                <SelectTrigger size="sm" className="h-8 w-36 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all" className="text-xs">All storage</SelectItem>
                  {storageClasses.map((c) => (
                    <SelectItem key={c} value={c} className="text-xs">{c}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <SortableHead sortKey="name" sort={stockSort} onSort={(k) => setStockSort((s) => nextSortState(s, k))} className="text-xs">Drug</SortableHead>
                <SortableHead sortKey="drug_class" sort={stockSort} onSort={(k) => setStockSort((s) => nextSortState(s, k))} className="text-xs">Class</SortableHead>
                <SortableHead sortKey="ndc" sort={stockSort} onSort={(k) => setStockSort((s) => nextSortState(s, k))} className="text-xs">NDC</SortableHead>
                <SortableHead sortKey="location" sort={stockSort} onSort={(k) => setStockSort((s) => nextSortState(s, k))} className="text-xs">Location</SortableHead>
                <SortableHead sortKey="storage_class" sort={stockSort} onSort={(k) => setStockSort((s) => nextSortState(s, k))} className="text-xs">Storage class</SortableHead>
                <SortableHead sortKey="quantity" sort={stockSort} onSort={(k) => setStockSort((s) => nextSortState(s, k))} className="text-right text-xs">On hand</SortableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {stockRows.map((item) => (
                <TableRow
                  key={`${item.ndc}-${item.location}`}
                  className={cn(
                    "cursor-pointer",
                    item.ndc === drugNdc && "bg-primary/5 shadow-[inset_2px_0_0_0_var(--primary)]",
                  )}
                  onClick={() => setDrugNdc(item.ndc)}
                >
                  <TableCell className="max-w-[26rem] text-xs">
                    <DrugName name={item.name} fallback={item.ndc} />
                  </TableCell>
                  <TableCell className="max-w-44 truncate text-xs text-muted-foreground" title={item.drug_class ?? undefined}>
                    {item.drug_class ?? "—"}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{item.ndc}</TableCell>
                  <TableCell className="text-xs">{item.location}</TableCell>
                  <TableCell className="text-xs">
                    {item.storage_class ? <Badge variant="outline" className="text-[10px]">{item.storage_class}</Badge> : "—"}
                  </TableCell>
                  <TableCell className="text-right text-xs tabular-nums">{item.quantity}</TableCell>
                </TableRow>
              ))}
              {stockRows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="py-6 text-center text-xs text-muted-foreground">
                    No shelf positions match the current filters.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
