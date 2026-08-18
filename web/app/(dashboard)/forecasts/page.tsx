"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, XAxis, YAxis } from "recharts";
import { Bot, CheckCircle2, PencilLine, RotateCcw, Sparkles, TrendingUp, Truck, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Separator } from "@/components/ui/separator";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { Callout } from "@/components/dashboard/Callout";
import { useCopilot } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { useOrders } from "@/lib/orders-context";
import { forecastFor, forecastableItemIds, inventoryFor, isoPlusDays, parLevel, suppliers } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

const chartConfig: ChartConfig = {
  actual: { label: "Actual usage", color: "var(--chart-2)" },
  forecast: { label: "Forecasted usage", color: "var(--chart-1)" },
  band: { label: "Confidence interval", color: "var(--chart-1)" },
};

// Surge tiers driving both the badge tone and the emergency-plan copy —
// 100% is the model's baseline forecast, 300% is a full epidemic surge.
function surgeTier(pct: number): { label: string; tone: StatusTone } {
  if (pct <= 120) return { label: "Standard", tone: "normal" };
  if (pct <= 200) return { label: "Elevated Demand", tone: "warning" };
  return { label: "Epidemic Surge / Emergency", tone: "critical" };
}

export default function ForecastsPage() {
  const router = useRouter();
  const { setFocus, requestEmergencyPlan } = useCopilot();
  const { facilityId, facility } = useFacility();
  const { addOrder } = useOrders();

  const items = useMemo(() => inventoryFor(facilityId), [facilityId]);
  const forecastable = useMemo(() => new Set(forecastableItemIds()), []);

  // Read ?sku= without useSearchParams() — that hook forces a Suspense
  // boundary that never resumes on a direct load of this already-"use
  // client" route (see UX-01, the same trap on /audit).
  const [skuParam, setSkuParam] = useState<string | null>(null);
  useEffect(() => {
    setSkuParam(new URLSearchParams(window.location.search).get("sku"));
  }, []);
  const validSkuParam = skuParam && items.some((i) => i.id === skuParam) ? skuParam : null;

  // Prefer a SKU with a trained model so the page opens on a working chart;
  // an explicit deep link (e.g. "View forecast" from Inventory) wins even
  // if that SKU has no model — the empty state below is the honest answer.
  const [itemId, setItemId] = useState(
    validSkuParam ?? items.find((i) => forecastable.has(i.id))?.id ?? items[0]?.id,
  );
  useEffect(() => {
    if (validSkuParam) setItemId(validSkuParam);
  }, [validSkuParam]);

  const [editingQty, setEditingQty] = useState(false);
  const [surgePct, setSurgePct] = useState(100);
  // null = follow the scenario slider's suggestion; a number = the user
  // typed an explicit override via Adjust Quantity. Moving the slider again
  // clears the override, so the suggestion always tracks the active
  // scenario unless the user is actively overriding it.
  const [manualQuantity, setManualQuantity] = useState<number | null>(null);
  // Per-SKU so declining one drug's suggestion doesn't hide the others.
  const [declinedIds, setDeclinedIds] = useState<Set<string>>(new Set());
  const [acceptedIds, setAcceptedIds] = useState<Set<string>>(new Set());

  // Falls back to the first SKU when the selected one isn't stocked at the
  // facility you just switched to, same pattern as /audit.
  const item = items.find((i) => i.id === itemId) ?? items[0];
  // Joins the trained model (if any) to this facility's actual stock and
  // burn rate — same SKU ids as Inventory, so this can't disagree with it
  // the way the old parallel `fc-*` drug list used to.
  const forecast = useMemo(() => forecastFor(facilityId, item.id), [facilityId, item.id]);

  const declined = declinedIds.has(item.id);
  const accepted = acceptedIds.has(item.id);

  function selectItem(id: string) {
    setItemId(id);
    setEditingQty(false);
    setSurgePct(100);
    setManualQuantity(null);
    const next = items.find((i) => i.id === id)!;
    const model = forecastFor(facilityId, id);
    setFocus({
      kind: "sku",
      label: next.drugName,
      detail: model ? `${model.model} forecast · ${model.confidence}% confidence` : "No forecast model trained for this SKU",
      itemId: next.id,
    });
  }

  function declineSuggestion() {
    setDeclinedIds((prev) => new Set(prev).add(item.id));
    toast("AI suggestion declined.", { description: `No order was created for ${item.drugName}.` });
  }

  function restoreSuggestion() {
    setDeclinedIds((prev) => {
      const next = new Set(prev);
      next.delete(item.id);
      return next;
    });
  }

  const surgeMultiplier = surgePct / 100;
  const tier = surgeTier(surgePct);

  // The model's own baseline predicted rate (not scaled by the scenario
  // slider) — this is what defines the par level the suggestion targets.
  // Using the forecast's rate rather than the item's historical burn rate
  // keeps this consistent with the card's own claim: "generated from the
  // confidence forecast above."
  const baselineAvgDailyForecast = useMemo(() => {
    if (!forecast) return 0;
    const points = forecast.series.filter((p) => p.forecast != null).map((p) => p.forecast!);
    if (points.length === 0) return 0;
    return points.reduce((sum, v) => sum + v, 0) / points.length;
  }, [forecast]);

  // Order enough to bring stock up to a 30-day par level, not a stored
  // literal — a facility already sitting on plenty of stock gets a smaller
  // suggestion than one running low, and the scenario slider scales the gap
  // rather than an arbitrary base quantity. An explicit manual edit
  // overrides it until the slider moves again.
  const par = forecast ? parLevel(baselineAvgDailyForecast) : 0;
  const suggestedQuantity = forecast ? Math.ceil(Math.max(1, par - item.currentStock) * surgeMultiplier) : 0;
  const quantity = manualQuantity ?? suggestedQuantity;

  const chartData = useMemo(
    () =>
      forecast
        ? forecast.series.map((p) => ({
            date: p.date.slice(5),
            actual: p.actual,
            forecast: p.forecast != null ? Math.round(p.forecast * surgeMultiplier) : null,
            band:
              p.forecastLow != null && p.forecastHigh != null
                ? [Math.round(p.forecastLow * surgeMultiplier), Math.round(p.forecastHigh * surgeMultiplier)]
                : undefined,
          }))
        : [],
    [forecast, surgeMultiplier],
  );

  // Walk the forecast window day-by-day at the scaled burn rate to find
  // when current stock actually runs out — this is what drives the red
  // depletion line, not a hardcoded number.
  const depletion = useMemo(() => {
    if (!forecast) return null;
    let remaining = item.currentStock;
    let day = 0;
    for (const p of forecast.series) {
      if (p.forecast == null) continue;
      day += 1;
      remaining -= p.forecast * surgeMultiplier;
      if (remaining <= 0) return { days: day, date: p.date.slice(5) };
    }
    return null;
  }, [forecast, item.currentStock, surgeMultiplier]);

  // The last actual-usage day — where the dashed forecast line picks up.
  // Marking it explicitly beats leaving the actual/forecast boundary to be
  // inferred from a change in line style alone.
  const todayLabel = useMemo(() => {
    if (!forecast) return null;
    const actualPoints = forecast.series.filter((p) => p.actual != null);
    return actualPoints.length > 0 ? actualPoints[actualPoints.length - 1].date.slice(5) : null;
  }, [forecast]);

  // Derived from the same scaled series the chart draws, so this can never
  // disagree with the quantity shown next to it.
  const avgDailyForecast = useMemo(() => {
    if (!forecast) return 0;
    const points = forecast.series.filter((p) => p.forecast != null).map((p) => p.forecast! * surgeMultiplier);
    if (points.length === 0) return 0;
    return points.reduce((sum, v) => sum + v, 0) / points.length;
  }, [forecast, surgeMultiplier]);
  const coverageDays = forecast && avgDailyForecast > 0 ? Math.round(quantity / avgDailyForecast) : 0;

  // The order arrives at (today + lead time); if stock runs out first,
  // standard shipping alone won't cover the gap.
  const leadTimeRisk = forecast != null && depletion != null && depletion.days <= forecast.purchaseOrder.leadTimeDays;

  const totalCost = forecast ? quantity * forecast.purchaseOrder.unitCost : 0;

  // Hands off to the order pipeline: lands in /orders as a draft awaiting
  // review rather than dispatching straight to the supplier.
  function acceptSuggestion() {
    if (!forecast) return;
    const supplier = suppliers.find((s) => s.name === forecast.purchaseOrder.supplier) ?? suppliers[0];
    const order = addOrder({
      facilityId,
      supplierId: supplier.id,
      drugId: item.id,
      drugName: item.drugName,
      quantity,
      unit: forecast.purchaseOrder.unit,
      unitCost: forecast.purchaseOrder.unitCost,
      shipping: supplier.shippingFlat,
      status: "draft",
      source: "ai_suggestion",
      expectedDelivery: isoPlusDays(supplier.leadTimeDays),
      note:
        surgePct === 100
          ? `Generated from ${forecast.confidence}% confidence forecast.`
          : `Generated from ${forecast.confidence}% confidence forecast at ${surgeMultiplier.toFixed(1)}× surge load.`,
    });
    setAcceptedIds((prev) => new Set(prev).add(item.id));
    toast.success(`Draft order ${order.id} created.`, {
      description: `${quantity} ${forecast.purchaseOrder.unit} of ${item.drugName} for ${facility.name}.`,
      action: { label: "Review", onClick: () => router.push("/orders") },
    });
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Restock & Forecasts</h1>
          <p className="text-xs text-muted-foreground">Burn-rate history and ML-predicted demand at {facility.name}.</p>
        </div>
        <Select value={item.id} onValueChange={selectItem}>
          <SelectTrigger size="sm" className="h-8 w-56 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {items.map((i) => (
                <SelectItem key={i.id} value={i.id}>
                  {i.drugName}
                  {!forecastable.has(i.id) && <span className="text-muted-foreground"> (no model)</span>}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </div>

      {/* Both columns start with a Card (chart / no-forecast placeholder on
          the left, AI suggestion / dismissed / no-model on the right) so
          they align at the same top edge — the header row above used to
          live inside the left column only, pushing its Card down past the
          right column's Card by that row's height. */}
      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
      <div className="flex flex-col gap-3">
        {!forecast ? (
          <Card className="gap-2 py-4">
            <CardContent className="flex flex-col items-center gap-2 px-4 py-16 text-center">
              <TrendingUp className="size-6 text-muted-foreground/40" />
              <p className="text-sm font-medium">No forecast model trained for {item.drugName}</p>
              <p className="max-w-sm text-xs text-muted-foreground">
                Prophet and XGBoost models are only trained for the network&apos;s highest-volume SKUs today. Pick another drug from
                the selector above.
              </p>
            </CardContent>
          </Card>
        ) : (
        <Card className="gap-3 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">{item.drugName} — 60 day actuals vs. 30 day forecast</CardTitle>
            <CardDescription className="flex flex-wrap items-center gap-1.5 text-xs">
              <Badge variant="secondary" className="font-normal">Model: {forecast.model}</Badge>
              <span className="text-muted-foreground">|</span>
              <Badge variant="secondary" className="font-normal">Seasonality: {forecast.seasonalityFactor}</Badge>
              <span className="text-muted-foreground">|</span>
              <Badge variant="secondary" className="font-normal">Confidence: {forecast.confidence}%</Badge>
            </CardDescription>
          </CardHeader>

          <div className="flex flex-col gap-2.5 border-y bg-muted/30 px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium">Scenario Simulator — What-If Analysis</span>
                <StatusBadge tone={tier.tone}>{tier.label}</StatusBadge>
              </div>
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {surgeMultiplier.toFixed(1)}× baseline load · stock depletes in{" "}
                <span className={cn("font-semibold", tier.tone === "critical" ? "text-red-500" : "text-foreground")}>
                  {depletion ? `${depletion.days}d` : "30d+"}
                </span>
              </span>
            </div>
            <div className="relative pb-1">
              <Slider
                value={[surgePct]}
                min={100}
                max={300}
                step={10}
                // Moving the slider re-suggests the order quantity — an
                // earlier manual override (via Adjust Quantity) no longer
                // applies to a different scenario.
                onValueChange={([v]) => {
                  setSurgePct(v);
                  setManualQuantity(null);
                }}
                aria-label="Demand surge scenario, from 1x to 3x baseline load"
              />
              {/* Tick marks at the surgeTier() boundaries so the named
                  tiers are targetable, not just something found by dragging. */}
              <div className="pointer-events-none absolute inset-x-0 top-0.5 h-1">
                {[120, 200].map((v) => (
                  <span
                    key={v}
                    className="absolute top-0 h-1 w-px -translate-x-1/2 bg-foreground/30"
                    style={{ left: `${((v - 100) / 200) * 100}%` }}
                  />
                ))}
              </div>
            </div>
            <div className="flex justify-between text-[10px] text-muted-foreground">
              <span>1× Standard</span>
              <span>3× Epidemic Surge</span>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="h-7 w-fit gap-1.5 self-start text-xs"
              onClick={() => requestEmergencyPlan({ drugName: item.drugName, surgePct, depletionDays: depletion ? depletion.days : null })}
            >
              <Bot className="size-3.5" />
              Generate emergency supply plan for current load
            </Button>
          </div>

          <CardContent className="px-2">
            <ChartContainer config={chartConfig} className="aspect-auto h-72 w-full">
              <ComposedChart data={chartData} margin={{ left: 4, right: 12, top: 8, bottom: 0 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis dataKey="date" tickLine={false} axisLine={false} fontSize={10} interval={6} />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  width={36}
                  fontSize={10}
                  label={{ value: `${forecast?.purchaseOrder.unit ?? "units"}/day`, angle: -90, position: "insideLeft", fontSize: 10, fill: "var(--muted-foreground)" }}
                />
                <ChartTooltip content={<ChartTooltipContent indicator="line" />} />
                <Area dataKey="band" stroke="none" fill="var(--color-forecast)" fillOpacity={0.15} isAnimationActive={false} connectNulls />
                <Line dataKey="actual" stroke="var(--color-actual)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
                <Line dataKey="forecast" stroke="var(--color-forecast)" strokeWidth={2} strokeDasharray="5 3" dot={false} isAnimationActive={false} connectNulls={false} />
                {todayLabel && (
                  <ReferenceLine
                    x={todayLabel}
                    stroke="var(--muted-foreground)"
                    strokeDasharray="2 2"
                    strokeWidth={1}
                    label={{ value: "Today", position: "insideBottomLeft", fill: "var(--muted-foreground)", fontSize: 10 }}
                  />
                )}
                {depletion && (
                  <ReferenceLine
                    x={depletion.date}
                    stroke="var(--color-destructive, #ef4444)"
                    strokeDasharray="4 3"
                    strokeWidth={1.5}
                    label={{ value: `Stockout · ${depletion.days}d`, position: "insideTopLeft", fill: "#ef4444", fontSize: 10 }}
                  />
                )}
                <ChartLegend content={<ChartLegendContent />} />
              </ComposedChart>
            </ChartContainer>
          </CardContent>
        </Card>
        )}
      </div>

      <div className="flex flex-col gap-3">
        {!forecast ? (
          <Card className="gap-2 border-dashed py-3">
            <CardContent className="flex items-center gap-1.5 px-4 text-xs text-muted-foreground">
              <Sparkles className="size-3.5" />
              No AI purchase suggestion — {item.drugName} has no forecast model.
            </CardContent>
          </Card>
        ) : declined ? (
          <Card className="gap-2 border-dashed py-3">
            <CardContent className="flex items-center justify-between gap-2 px-4 text-xs">
              <span className="flex items-center gap-1.5 text-muted-foreground">
                <Sparkles className="size-3.5" />
                AI suggestion dismissed for {item.drugName}.
              </span>
              <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs" onClick={restoreSuggestion}>
                <RotateCcw className="size-3.5" />
                Restore
              </Button>
            </CardContent>
          </Card>
        ) : (
        <Card className="gap-3 border-primary/30 py-4">
          <CardHeader className="px-4">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Sparkles className="size-4 text-primary" />
              AI Purchase Order
              <Badge variant="secondary" className="ml-auto text-[10px] font-normal">Suggestion</Badge>
            </CardTitle>
            <CardDescription className="text-xs">
              Generated from the {forecast.confidence}% confidence forecast above, for {facility.name}. Review before it becomes an order.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 px-4 text-xs">
            <div className="flex justify-between"><span className="text-muted-foreground">Supplier</span><span className="font-medium">{forecast.purchaseOrder.supplier}</span></div>
            <div className="flex justify-between"><span className="text-muted-foreground">Lead time</span><span className="font-mono tabular-nums">{forecast.purchaseOrder.leadTimeDays} days</span></div>

            {leadTimeRisk && (
              <Callout tone="critical" className="flex items-start gap-2">
                <StatusBadge tone="critical" className="mt-0.5 shrink-0">Lead time risk</StatusBadge>
                <div className="flex flex-col gap-1">
                  <span>
                    Standard shipping arrives in {forecast.purchaseOrder.leadTimeDays}d — stock depletes in {depletion!.days}d.
                  </span>
                  <Button
                    variant="link"
                    size="sm"
                    className="h-auto w-fit p-0 text-xs text-red-700 underline dark:text-red-400"
                    onClick={() => requestEmergencyPlan({ drugName: item.drugName, surgePct, depletionDays: depletion ? depletion.days : null })}
                  >
                    Generate emergency supply plan
                  </Button>
                </div>
              </Callout>
            )}

            <Separator />
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Order quantity</span>
              {editingQty ? (
                <Input
                  type="number"
                  min={1}
                  value={quantity}
                  onChange={(e) => setManualQuantity(Math.max(1, Number(e.target.value)))}
                  className="h-7 w-24 text-right font-mono text-xs tabular-nums"
                  autoFocus
                  onBlur={() => setEditingQty(false)}
                />
              ) : (
                <span className="font-mono font-medium tabular-nums">{quantity} {forecast.purchaseOrder.unit}</span>
              )}
            </div>
            <p className="text-muted-foreground">
              <span className="font-mono tabular-nums">{quantity} {forecast.purchaseOrder.unit}</span> covers{" "}
              <span className="font-mono tabular-nums">{coverageDays} days</span> of forecasted demand at{" "}
              {surgeMultiplier.toFixed(1)}× load, including safety buffer.
            </p>
            <Separator />
            <div className="flex justify-between text-sm font-semibold"><span>Estimated total</span><span className="font-mono tabular-nums">${totalCost.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span></div>
          </CardContent>
          <CardFooter className="flex flex-wrap gap-2 px-4">
            <Button variant="ghost" size="sm" className="h-8 text-xs" onClick={declineSuggestion} disabled={accepted}>
              <X data-icon="inline-start" />
              Decline
            </Button>
            <Button variant="outline" size="sm" className="h-8 flex-1 text-xs" onClick={() => setEditingQty(true)} disabled={accepted}>
              <PencilLine data-icon="inline-start" />
              Adjust Quantity
            </Button>
            <Button size="sm" className="h-8 flex-1 text-xs" disabled={accepted} onClick={acceptSuggestion}>
              {accepted ? <CheckCircle2 data-icon="inline-start" /> : <Truck data-icon="inline-start" />}
              {accepted ? "Draft created" : "Create Draft Order"}
            </Button>
          </CardFooter>
        </Card>
        )}
      </div>
      </div>
    </div>
  );
}
