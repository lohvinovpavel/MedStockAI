"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, XAxis, YAxis } from "recharts";
import { Building2, Refrigerator, ThermometerSun } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { StatusBadge } from "@/components/dashboard/StatusBadge";
import { Callout } from "@/components/dashboard/Callout";
import { apiFetch } from "@/lib/api";

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
interface StockItem { ndc: string; name: string | null; location: string; quantity: number; storage_class: string | null }
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
    return [...groups.entries()];
  }, [excursions]);

  if (loading) return <div className="p-6 text-sm text-muted-foreground">Loading warehouse…</div>;

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
                        <TableHead className="text-xs">Affected drug</TableHead>
                        <TableHead className="text-xs">On hand</TableHead>
                        <TableHead className="text-xs">Required</TableHead>
                        <TableHead className="text-xs">Observed</TableHead>
                        <TableHead className="text-xs">Hours</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {items.map((e) => (
                        <TableRow key={e.ndc}>
                          <TableCell className="max-w-[26rem] truncate text-xs" title={e.drug}>{e.drug}</TableCell>
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
              <div className="flex gap-2">
                <Select value={drugNdc ?? undefined} onValueChange={setDrugNdc}>
                  <SelectTrigger size="sm" className="h-8 w-64 text-xs">
                    <SelectValue placeholder="Drug" />
                  </SelectTrigger>
                  <SelectContent>
                    {drugs.map((d) => (
                      <SelectItem key={d.ndc} value={d.ndc} className="text-xs">
                        {(d.name ?? d.ndc).slice(0, 60)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
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
          <CardTitle className="text-sm">Stock by location</CardTitle>
          <CardDescription className="text-xs">
            {stock.length} shelf positions at {facility?.name}.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">Drug</TableHead>
                <TableHead className="text-xs">NDC</TableHead>
                <TableHead className="text-xs">Location</TableHead>
                <TableHead className="text-xs">Storage class</TableHead>
                <TableHead className="text-right text-xs">On hand</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {stock.map((item) => (
                <TableRow key={`${item.ndc}-${item.location}`}>
                  <TableCell className="max-w-[30rem] truncate text-xs" title={item.name ?? undefined}>
                    {item.name ?? "—"}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{item.ndc}</TableCell>
                  <TableCell className="text-xs">{item.location}</TableCell>
                  <TableCell className="text-xs">
                    {item.storage_class ? <Badge variant="outline" className="text-[10px]">{item.storage_class}</Badge> : "—"}
                  </TableCell>
                  <TableCell className="text-right text-xs tabular-nums">{item.quantity}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
