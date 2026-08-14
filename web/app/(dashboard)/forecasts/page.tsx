"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, XAxis, YAxis } from "recharts";
import { Bot, CheckCircle2, PencilLine, Truck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Separator } from "@/components/ui/separator";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { useCopilot } from "@/lib/copilot-context";
import { forecastDrugs } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

const chartConfig: ChartConfig = {
  actual: { label: "Actual usage", color: "var(--chart-2)" },
  forecast: { label: "Forecasted usage", color: "var(--chart-1)" },
};

// Surge tiers driving both the badge tone and the emergency-plan copy —
// 100% is the model's baseline forecast, 300% is a full epidemic surge.
function surgeTier(pct: number): { label: string; tone: StatusTone } {
  if (pct <= 120) return { label: "Standard", tone: "normal" };
  if (pct <= 200) return { label: "Elevated Demand", tone: "warning" };
  return { label: "Epidemic Surge / Emergency", tone: "critical" };
}

export default function ForecastsPage() {
  const { setFocus, requestEmergencyPlan } = useCopilot();
  const [drugId, setDrugId] = useState(forecastDrugs[0].id);
  const [dispatched, setDispatched] = useState(false);
  const [editingQty, setEditingQty] = useState(false);
  const [surgePct, setSurgePct] = useState(100);

  const drug = useMemo(() => forecastDrugs.find((d) => d.id === drugId)!, [drugId]);
  const [quantity, setQuantity] = useState(drug.purchaseOrder.quantity);

  function selectDrug(id: string) {
    setDrugId(id);
    setDispatched(false);
    setEditingQty(false);
    setSurgePct(100);
    const next = forecastDrugs.find((d) => d.id === id)!;
    setQuantity(next.purchaseOrder.quantity);
    setFocus({ kind: "sku", label: next.name, detail: `${next.model} forecast · ${next.confidence}% confidence` });
  }

  const surgeMultiplier = surgePct / 100;
  const tier = surgeTier(surgePct);

  const chartData = drug.series.map((p) => ({
    date: p.date.slice(5),
    actual: p.actual,
    forecast: p.forecast != null ? Math.round(p.forecast * surgeMultiplier) : null,
    band:
      p.forecastLow != null && p.forecastHigh != null
        ? [Math.round(p.forecastLow * surgeMultiplier), Math.round(p.forecastHigh * surgeMultiplier)]
        : undefined,
  }));

  // Walk the forecast window day-by-day at the scaled burn rate to find
  // when current stock actually runs out — this is what drives the red
  // depletion line, not a hardcoded number.
  const depletion = useMemo(() => {
    let remaining = drug.currentStock;
    let day = 0;
    for (const p of drug.series) {
      if (p.forecast == null) continue;
      day += 1;
      remaining -= p.forecast * surgeMultiplier;
      if (remaining <= 0) return { days: day, date: p.date.slice(5) };
    }
    return null;
  }, [drug, surgeMultiplier]);

  const totalCost = quantity * drug.purchaseOrder.unitCost;

  return (
    <div className="grid gap-4 p-4 lg:grid-cols-[60%_40%]">
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Restock & Forecasts</h1>
            <p className="text-xs text-muted-foreground">Burn-rate history and ML-predicted demand.</p>
          </div>
          <Select value={drugId} onValueChange={selectDrug}>
            <SelectTrigger size="sm" className="h-8 w-56 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {forecastDrugs.map((d) => (
                  <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>

        <Card className="gap-3 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">{drug.name} — 60 day actuals vs. 30 day forecast</CardTitle>
            <CardDescription className="flex flex-wrap items-center gap-1.5 text-xs">
              <Badge variant="secondary" className="font-normal">Model: {drug.model}</Badge>
              <span className="text-muted-foreground">|</span>
              <Badge variant="secondary" className="font-normal">Seasonality: {drug.seasonalityFactor}</Badge>
              <span className="text-muted-foreground">|</span>
              <Badge variant="secondary" className="font-normal">Confidence: {drug.confidence}%</Badge>
            </CardDescription>
          </CardHeader>

          <div className="flex flex-col gap-2.5 border-y bg-muted/30 px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium">Scenario Simulator — What-If Analysis</span>
                <StatusBadge tone={tier.tone}>{tier.label}</StatusBadge>
              </div>
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {surgePct}% load · stock depletes in{" "}
                <span className={cn("font-semibold", tier.tone === "critical" ? "text-red-500" : "text-foreground")}>
                  {depletion ? `${depletion.days}d` : "30d+"}
                </span>
              </span>
            </div>
            <Slider
              value={[surgePct]}
              min={100}
              max={300}
              step={10}
              onValueChange={([v]) => setSurgePct(v)}
              aria-label="Demand surge scenario"
            />
            <div className="flex justify-between text-[10px] text-muted-foreground">
              <span>Standard (100%)</span>
              <span>Epidemic Surge / Emergency (+200%)</span>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="h-7 w-fit gap-1.5 self-start text-xs"
              onClick={() => requestEmergencyPlan({ drugName: drug.name, surgePct, depletionDays: depletion ? depletion.days : null })}
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
                <YAxis tickLine={false} axisLine={false} width={28} fontSize={10} />
                <ChartTooltip content={<ChartTooltipContent indicator="line" />} />
                <Area dataKey="band" stroke="none" fill="var(--color-forecast)" fillOpacity={0.15} isAnimationActive={false} connectNulls />
                <Line dataKey="actual" stroke="var(--color-actual)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
                <Line dataKey="forecast" stroke="var(--color-forecast)" strokeWidth={2} strokeDasharray="5 3" dot={false} isAnimationActive={false} connectNulls={false} />
                {depletion && (
                  <ReferenceLine
                    x={depletion.date}
                    stroke="var(--color-destructive, #ef4444)"
                    strokeDasharray="4 3"
                    strokeWidth={1.5}
                    label={{ value: `Stockout · ${depletion.days}d`, position: "insideTopLeft", fill: "#ef4444", fontSize: 10 }}
                  />
                )}
              </ComposedChart>
            </ChartContainer>
          </CardContent>
        </Card>
      </div>

      <div className="flex flex-col gap-3">
        <Card className="gap-3 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">AI Purchase Order</CardTitle>
            <CardDescription className="text-xs">Generated from the {drug.confidence}% confidence forecast above.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 px-4 text-xs">
            <div className="flex justify-between"><span className="text-muted-foreground">Supplier</span><span className="font-medium">{drug.purchaseOrder.supplier}</span></div>
            <div className="flex justify-between"><span className="text-muted-foreground">Lead time</span><span className="font-mono tabular-nums">{drug.purchaseOrder.leadTimeDays} days</span></div>
            <Separator />
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Order quantity</span>
              {editingQty ? (
                <Input
                  type="number"
                  min={1}
                  value={quantity}
                  onChange={(e) => setQuantity(Math.max(1, Number(e.target.value)))}
                  className="h-7 w-24 text-right font-mono text-xs tabular-nums"
                  autoFocus
                  onBlur={() => setEditingQty(false)}
                />
              ) : (
                <span className="font-mono font-medium tabular-nums">{quantity} {drug.purchaseOrder.unit}</span>
              )}
            </div>
            <p className="text-muted-foreground">
              <span className="font-mono tabular-nums">{quantity} {drug.purchaseOrder.unit}</span> covers <span className="font-mono tabular-nums">{drug.purchaseOrder.coverageDays} days</span> of forecasted demand, including safety buffer.
            </p>
            <Separator />
            <div className="flex justify-between text-sm font-semibold"><span>Estimated total</span><span className="font-mono tabular-nums">${totalCost.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span></div>
          </CardContent>
          <CardFooter className="flex gap-2 px-4">
            <Button variant="outline" size="sm" className="h-8 flex-1 text-xs" onClick={() => setEditingQty(true)} disabled={dispatched}>
              <PencilLine data-icon="inline-start" />
              Adjust Quantity
            </Button>
            <Button
              size="sm"
              className="h-8 flex-1 text-xs"
              disabled={dispatched}
              onClick={() => {
                setDispatched(true);
                toast.success(`PO for ${quantity} ${drug.purchaseOrder.unit} of ${drug.name} dispatched to ${drug.purchaseOrder.supplier}.`);
              }}
            >
              {dispatched ? <CheckCircle2 data-icon="inline-start" /> : <Truck data-icon="inline-start" />}
              {dispatched ? "Dispatched" : "Approve & Dispatch PO"}
            </Button>
          </CardFooter>
        </Card>
      </div>
    </div>
  );
}
