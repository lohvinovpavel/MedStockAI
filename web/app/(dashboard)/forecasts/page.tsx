"use client";

/**
 * Restock & Forecasts — real data from the prediction service (issue #7).
 *
 * The chart draws stored forecast_point rows (spec E1); the surge slider is
 * server-side arithmetic over the same rows (E3), so a screenshot of this
 * page can always be traced to a run_id. Days-of-supply comes from the one
 * place the formula lives (E2). When consumption data has outrun the newest
 * run (latest_data > data_through) the page auto-triggers POST
 * /forecast/runs once — for roles that hold forecast:run — instead of
 * silently charting a stale forecast.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, XAxis, YAxis } from "recharts";
import { AlertTriangle, RefreshCw, TrendingUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Separator } from "@/components/ui/separator";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { useSession } from "@/lib/session";
import { can } from "@/lib/rbac";
import { apiFetch, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

type AtRiskItem = {
  ndc: string;
  rxcui: string | null;
  name: string | null;
  quantity: number;
  days_of_supply: number;
  days_of_supply_p90: number | null;
  depletion_date: string | null;
  basis: string | null;
  reorder_point: number | null;
  in_shortage: boolean;
};

type AtRisk = {
  within_days: number;
  run_id: string | null;
  generated_at: string | null;
  data_through: string | null;
  latest_data: string | null;
  items: AtRiskItem[];
};

type Forecast = {
  rxcui: string;
  surge_pct: number;
  scenario: "standard" | "surge";
  run_id: string | null;
  model_version: string | null;
  generated_at: string | null;
  data_through: string | null;
  latest_data: string | null;
  history: { date: string; quantity: number }[];
  stock_history: { date: string; quantity: number }[];
  forecast: { date: string; p10: number; p50: number; p90: number }[];
  depletion: {
    quantity: number;
    date: string | null;
    days: number | null;
    days_p90: number | null;
    basis: string | null;
    reason: string | null;
  } | null;
  baseline_depletion?: { date: string | null; days: number | null };
  reason: string | null;
};

const usageChartConfig: ChartConfig = {
  actual: { label: "Actual usage", color: "var(--chart-2)" },
  forecast: { label: "Forecast p50", color: "var(--chart-1)" },
  band: { label: "p10–p90 band", color: "var(--chart-1)" },
};

const stockChartConfig: ChartConfig = {
  stockActual: { label: "Recorded stock", color: "var(--chart-2)" },
  stock: { label: "Projected stock (p50)", color: "var(--chart-3)" },
  stockBand: { label: "if demand runs low–high", color: "var(--chart-3)" },
};

// Surge tiers driving the badge tone — 100% is the stored run's baseline,
// 300% a full epidemic surge. The multiplier itself is applied server-side.
function surgeTier(pct: number): { label: string; tone: StatusTone } {
  if (pct <= 120) return { label: "Standard", tone: "normal" };
  if (pct <= 200) return { label: "Elevated Demand", tone: "warning" };
  return { label: "Epidemic Surge / Emergency", tone: "critical" };
}

function basisLabel(basis: string | null, reason: string | null): string {
  if (reason === "no_history") return "unknown — no history";
  if (reason === "beyond_horizon") return "90+ days";
  if (basis === "trailing_mean") return "28-day trailing mean";
  return basis ?? "—";
}

export default function ForecastsPage() {
  const { user } = useSession();
  // Mirrors forecast:run in shared PERMS — the backend 403s on its own;
  // this only decides whether the button is offered.
  const canRun = can(user?.role, "runForecast");

  const [atRisk, setAtRisk] = useState<AtRisk | null>(null);
  const [atRiskError, setAtRiskError] = useState<string | null>(null);
  const [rxcui, setRxcui] = useState<string | null>(null);
  const [surgePct, setSurgePct] = useState(100);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [running, setRunning] = useState(false);
  const autoRan = useRef(false);

  const loadAtRisk = useCallback(async () => {
    try {
      const body: AtRisk = await apiFetch("prediction", "/at-risk?within_days=90");
      setAtRisk(body);
      setAtRiskError(null);
      // ?sku= deep link (NDC or RxCUI, e.g. from Inventory's "View forecast").
      // Read without useSearchParams() — that hook forces a Suspense boundary
      // that never resumes on a direct load of this "use client" route.
      const sku = new URLSearchParams(window.location.search).get("sku");
      const linked = sku ? body.items.find((i) => i.ndc === sku || i.rxcui === sku) : null;
      setRxcui((current) => linked?.rxcui ?? current ?? body.items.find((i) => i.rxcui)?.rxcui ?? null);
    } catch (e) {
      setAtRiskError(e instanceof ApiError ? e.message : "prediction service unreachable");
    }
  }, []);

  useEffect(() => {
    void loadAtRisk();
  }, [loadAtRisk]);

  const runForecast = useCallback(
    async (auto: boolean) => {
      setRunning(true);
      try {
        const body = await apiFetch("prediction", "/forecast/runs", { method: "POST" });
        toast.success(auto ? "Forecast re-run — data had changed since the last run." : "Forecast run complete.", {
          description: `${body.points_written} points across ${body.skus_forecast} SKUs (${body.skus_skipped} skipped for short history).`,
        });
        await loadAtRisk();
        setForecast(null); // refetched by the effect below against the new run
      } catch (e) {
        toast.error("Forecast run failed", {
          description: e instanceof ApiError ? e.message : String(e),
        });
      } finally {
        setRunning(false);
      }
    },
    [loadAtRisk],
  );

  // Client auto-refresh: consumption newer than the run → one automatic
  // re-run. Once per page load, and only for roles that may trigger it.
  useEffect(() => {
    if (!atRisk || autoRan.current || !canRun) return;
    if (atRisk.latest_data && atRisk.data_through && atRisk.latest_data > atRisk.data_through) {
      autoRan.current = true;
      void runForecast(true);
    }
  }, [atRisk, canRun, runForecast]);

  // Fetch the selected drug's forecast; the surge slider re-fetches because
  // the multiplier is applied by the server (E3) — debounced while dragging.
  useEffect(() => {
    if (!rxcui) return;
    const handle = setTimeout(async () => {
      try {
        const body: Forecast = await apiFetch(
          "prediction",
          `/forecast/${rxcui}?horizon_days=30&surge_pct=${surgePct}`,
        );
        setForecast(body);
      } catch {
        setForecast(null);
      }
    }, 250);
    return () => clearTimeout(handle);
  }, [rxcui, surgePct]);

  const items = atRisk?.items ?? [];
  const selected = items.find((i) => i.rxcui === rxcui) ?? null;
  const tier = surgeTier(surgePct);

  const chartData = useMemo(() => {
    if (!forecast) return [];
    // Stock burn-down: current on-hand minus cumulative forecast usage —
    // the decreasing curve that hits zero at the red depletion marker. Its
    // band comes from the usage quantiles: p10 usage drains slowest (upper
    // edge), p90 fastest (lower edge). Clipped at zero: shelves don't go
    // negative. Historical stock-per-day is not recorded anywhere (only a
    // live snapshot exists until B4 receiving events land), so the stock
    // series honestly starts at today's on-hand, not in the past.
    const onHand = forecast.depletion?.quantity ?? null;
    let rem50 = onHand;
    let remSlow = onHand; // p10 usage — stock lasts longer
    let remFast = onHand; // p90 usage — stock drains sooner
    // Recorded end-of-day stock (stock_daily; planted in the demo, B4
    // receiving events eventually) keyed by date for the history half.
    const recordedStock = new Map(forecast.stock_history.map((p) => [p.date, p.quantity]));
    const rows = [
      ...forecast.history.map((p) => ({ date: p.date.slice(5), actual: p.quantity as number | null, forecast: null as number | null, band: undefined as [number, number] | undefined, stockActual: (recordedStock.get(p.date) ?? null) as number | null, stock: null as number | null, stockBand: undefined as [number, number] | undefined })),
      ...forecast.forecast.map((p) => {
        if (rem50 != null) rem50 = Math.max(0, rem50 - p.p50);
        if (remSlow != null) remSlow = Math.max(0, remSlow - p.p10);
        if (remFast != null) remFast = Math.max(0, remFast - p.p90);
        return {
          date: p.date.slice(5),
          actual: null,
          forecast: p.p50,
          band: [p.p10, p.p90] as [number, number],
          stockActual: null,
          stock: rem50,
          stockBand: remFast != null && remSlow != null ? ([remFast, remSlow] as [number, number]) : undefined,
        };
      }),
    ];
    // Bridge point: give the forecast series (and its band, and the
    // burn-down's starting stock) the boundary date's values, so the dashed
    // lines grow out of the tip of the solid one instead of starting a day
    // later across a gap.
    const boundary = forecast.history.length - 1;
    if (boundary >= 0 && forecast.forecast.length > 0) {
      const lastActual = forecast.history[boundary].quantity;
      rows[boundary] = {
        ...rows[boundary],
        forecast: lastActual,
        band: [lastActual, lastActual],
        stock: onHand,
        stockBand: onHand != null ? [onHand, onHand] : undefined,
      };
    }
    return rows;
  }, [forecast]);

  const todayLabel = forecast?.history.length
    ? forecast.history[forecast.history.length - 1].date.slice(5)
    : null;
  const depletionLabel = forecast?.depletion?.date ? forecast.depletion.date.slice(5) : null;

  const noRunYet = atRisk !== null && atRisk.run_id === null;

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Restock & Forecasts</h1>
          <p className="text-xs text-muted-foreground">
            Consumption history and quantile demand forecast per drug — served from the latest stored run.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canRun && (
            <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs" disabled={running} onClick={() => runForecast(false)}>
              <RefreshCw className={cn("size-3.5", running && "animate-spin")} />
              {running ? "Running…" : "Run forecast"}
            </Button>
          )}
          <Select value={rxcui ?? undefined} onValueChange={setRxcui}>
            <SelectTrigger size="sm" className="h-8 w-64 text-xs">
              <SelectValue placeholder="Select a drug" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {items.filter((i) => i.rxcui).map((i) => (
                  <SelectItem key={i.ndc} value={i.rxcui!}>
                    {i.name ?? i.ndc}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>
      </div>

      {atRiskError && (
        <Card className="gap-2 border-dashed py-3">
          <CardContent className="px-4 text-xs text-muted-foreground">
            Could not reach the prediction service: {atRiskError}
          </CardContent>
        </Card>
      )}

      {noRunYet && (
        <Card className="gap-2 py-4">
          <CardContent className="flex flex-col items-center gap-2 px-4 py-12 text-center">
            <TrendingUp className="size-6 text-muted-foreground/40" />
            <p className="text-sm font-medium">No forecast run yet</p>
            <p className="max-w-sm text-xs text-muted-foreground">
              Forecasts are computed from recorded consumption and stored per run.
              {canRun ? " Run one to populate this page." : " A pharmacist or director can run one."}
            </p>
            {canRun && (
              <Button size="sm" className="mt-1 h-8 text-xs" disabled={running} onClick={() => runForecast(false)}>
                {running ? "Running…" : "Run forecast"}
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      {forecast && selected && (
        <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
          <Card className="gap-3 py-4">
            <CardHeader className="px-4">
              <CardTitle className="text-sm">
                {selected.name ?? selected.ndc} — 60 day actuals vs. 30 day forecast
              </CardTitle>
              <CardDescription className="flex flex-wrap items-center gap-1.5 text-xs">
                <Badge variant="secondary" className="font-normal">Model: {forecast.model_version ?? "—"}</Badge>
                <span className="text-muted-foreground">|</span>
                <Badge variant="secondary" className="font-normal">
                  Run: {forecast.run_id ? forecast.run_id.slice(0, 8) : "none"}
                </Badge>
                <span className="text-muted-foreground">|</span>
                <Badge variant="secondary" className="font-normal">Data through: {forecast.data_through ?? "—"}</Badge>
              </CardDescription>
            </CardHeader>

            <div className="flex flex-col gap-2.5 border-y bg-muted/30 px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium">Scenario Simulator — What-If Analysis</span>
                  <StatusBadge tone={tier.tone}>{tier.label}</StatusBadge>
                </div>
                <span className="font-mono text-xs tabular-nums text-muted-foreground">
                  {(surgePct / 100).toFixed(1)}× baseline demand · stock lasts{" "}
                  <span className={cn("font-semibold", tier.tone === "critical" ? "text-red-500" : "text-foreground")}>
                    {forecast.depletion?.days != null ? `${forecast.depletion.days}d` : basisLabel(null, forecast.depletion?.reason ?? null)}
                  </span>
                </span>
              </div>
              <Slider
                value={[surgePct]}
                min={100}
                max={300}
                step={10}
                onValueChange={([v]) => setSurgePct(v)}
                aria-label="Demand surge scenario, from 1x to 3x baseline demand"
              />
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>1× Standard</span>
                <span>3× Epidemic Surge</span>
              </div>
              {forecast.scenario === "surge" && forecast.baseline_depletion && (
                <p className="text-[11px] text-muted-foreground">
                  {forecast.baseline_depletion.days != null ? `${forecast.baseline_depletion.days} days normally` : "90+ days normally"}
                  {forecast.depletion?.days != null && ` — ${forecast.depletion.days} under this surge`}.
                </p>
              )}
            </div>

            <CardContent className="px-2">
              {forecast.forecast.length === 0 ? (
                <div className="flex flex-col items-center gap-2 px-4 py-16 text-center">
                  <TrendingUp className="size-6 text-muted-foreground/40" />
                  <p className="text-sm font-medium">No forecast for this drug</p>
                  <p className="max-w-sm text-xs text-muted-foreground">
                    {forecast.reason === "insufficient_history"
                      ? "Fewer than 21 days of history — a confident line drawn from a handful of points would be worse than an empty chart."
                      : "No stored forecast covers this drug yet."}
                  </p>
                </div>
              ) : (
                <div className="flex flex-col gap-1">
                  {/* Two date-aligned plots: usage answers "how fast is it
                      going", stock answers "how much is left". Same x-range,
                      so the data-through boundary and the depletion marker
                      line up vertically between them. */}
                  <p className="px-2 text-[11px] font-medium text-muted-foreground">Usage per day</p>
                  <ChartContainer config={usageChartConfig} className="aspect-auto h-48 w-full">
                    <ComposedChart data={chartData} margin={{ left: 4, right: 12, top: 8, bottom: 0 }} syncId="forecast">
                      <CartesianGrid vertical={false} strokeDasharray="3 3" />
                      <XAxis dataKey="date" tickLine={false} axisLine={false} fontSize={10} interval={6} />
                      <YAxis tickLine={false} axisLine={false} width={36} fontSize={10} />
                      <ChartTooltip content={<ChartTooltipContent indicator="line" />} />
                      <Area dataKey="band" stroke="none" fill="var(--color-forecast)" fillOpacity={0.15} isAnimationActive={false} connectNulls />
                      <Line dataKey="actual" stroke="var(--color-actual)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
                      <Line dataKey="forecast" stroke="var(--color-forecast)" strokeWidth={2} strokeDasharray="5 3" dot={false} isAnimationActive={false} connectNulls={false} />
                      {todayLabel && (
                        <ReferenceLine x={todayLabel} stroke="var(--muted-foreground)" strokeDasharray="2 2" strokeWidth={1}
                          label={{ value: "Data through", position: "insideBottomLeft", fill: "var(--muted-foreground)", fontSize: 10 }} />
                      )}
                      {depletionLabel && (
                        <ReferenceLine x={depletionLabel} stroke="var(--color-destructive, #ef4444)" strokeDasharray="4 3" strokeWidth={1} />
                      )}
                      <ChartLegend content={<ChartLegendContent />} />
                    </ComposedChart>
                  </ChartContainer>
                  <p className="px-2 pt-2 text-[11px] font-medium text-muted-foreground">
                    Stock on hand — recorded daily balance, projected forward to stockout
                  </p>
                  <ChartContainer config={stockChartConfig} className="aspect-auto h-40 w-full">
                    <ComposedChart data={chartData} margin={{ left: 4, right: 12, top: 8, bottom: 0 }} syncId="forecast">
                      <CartesianGrid vertical={false} strokeDasharray="3 3" />
                      <XAxis dataKey="date" tickLine={false} axisLine={false} fontSize={10} interval={6} />
                      <YAxis tickLine={false} axisLine={false} width={36} fontSize={10} />
                      <ChartTooltip content={<ChartTooltipContent indicator="line" />} />
                      <Area dataKey="stockBand" stroke="none" fill="var(--color-stock)" fillOpacity={0.15} isAnimationActive={false} connectNulls />
                      <Line dataKey="stockActual" stroke="var(--color-stockActual)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
                      <Line dataKey="stock" stroke="var(--color-stock)" strokeWidth={2} strokeDasharray="5 3" dot={false} isAnimationActive={false} connectNulls={false} />
                      {todayLabel && (
                        <ReferenceLine x={todayLabel} stroke="var(--muted-foreground)" strokeDasharray="2 2" strokeWidth={1} />
                      )}
                      {depletionLabel && (
                        <ReferenceLine x={depletionLabel} stroke="var(--color-destructive, #ef4444)" strokeDasharray="4 3" strokeWidth={1.5}
                          label={{ value: `Stockout · ${forecast.depletion!.days}d`, position: "insideTopLeft", fill: "#ef4444", fontSize: 10 }} />
                      )}
                      <ChartLegend content={<ChartLegendContent />} />
                    </ComposedChart>
                  </ChartContainer>
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="gap-3 py-4">
            <CardHeader className="px-4">
              <CardTitle className="text-sm">Days of supply</CardTitle>
              <CardDescription className="text-xs">
                On-hand stock against forecast demand, no incoming deliveries assumed.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 px-4 text-xs">
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {forecast.depletion?.days != null ? `${forecast.depletion.days}d` : basisLabel(null, forecast.depletion?.reason ?? null)}
                </span>
                {forecast.depletion?.days_p90 != null && forecast.depletion.days_p90 !== forecast.depletion.days && (
                  <span className="text-muted-foreground">or {forecast.depletion.days_p90}d if demand runs high</span>
                )}
              </div>
              <div className="flex justify-between"><span className="text-muted-foreground">On hand</span><span className="font-mono tabular-nums">{forecast.depletion?.quantity ?? selected.quantity}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Depletion date</span><span className="font-mono tabular-nums">{forecast.depletion?.date ?? "—"}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Basis</span><span>{basisLabel(forecast.depletion?.basis ?? null, forecast.depletion?.reason ?? null)}</span></div>
              {selected.in_shortage && (
                <StatusBadge tone="critical" className="w-fit gap-1">
                  <AlertTriangle className="size-3" />
                  National shortage reported
                </StatusBadge>
              )}
              <Separator />
              <p className="text-[11px] text-muted-foreground">
                Forecast generated {forecast.generated_at ? new Date(forecast.generated_at).toLocaleString() : "—"} from data through{" "}
                {forecast.data_through ?? "—"}. Run {forecast.run_id ? forecast.run_id.slice(0, 8) : "—"}.
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      {items.length > 0 && (
        <Card className="gap-2 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">At risk — worst first</CardTitle>
            <CardDescription className="text-xs">
              Drugs whose stock runs out within 90 days at forecast demand, or that carry an active shortage signal.
            </CardDescription>
          </CardHeader>
          <CardContent className="px-4">
            <Table>
              <TableHeader>
                <TableRow className="text-xs">
                  <TableHead>Drug</TableHead>
                  <TableHead className="text-right">On hand</TableHead>
                  <TableHead className="text-right">Days of supply</TableHead>
                  <TableHead className="text-right">If demand runs high</TableHead>
                  <TableHead>Depletion</TableHead>
                  <TableHead>Basis</TableHead>
                  <TableHead>Shortage</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.slice(0, 25).map((i) => (
                  <TableRow
                    key={i.ndc}
                    className={cn("cursor-pointer text-xs", i.rxcui === rxcui && "bg-muted/50")}
                    onClick={() => i.rxcui && setRxcui(i.rxcui)}
                  >
                    <TableCell className="max-w-64 truncate font-medium">{i.name ?? i.ndc}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{i.quantity}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      <span className={cn(i.days_of_supply <= 14 && "font-semibold text-red-500")}>{i.days_of_supply}d</span>
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {i.days_of_supply_p90 != null ? `${i.days_of_supply_p90}d` : "—"}
                    </TableCell>
                    <TableCell className="font-mono tabular-nums">{i.depletion_date ?? "—"}</TableCell>
                    <TableCell>{i.basis === "trailing_mean" ? "trailing mean" : i.basis ?? "—"}</TableCell>
                    <TableCell>
                      {i.in_shortage ? <StatusBadge tone="critical">shortage</StatusBadge> : <span className="text-muted-foreground">—</span>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {items.length > 25 && (
              <p className="mt-2 text-[11px] text-muted-foreground">Showing the 25 worst of {items.length} at-risk drugs.</p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
