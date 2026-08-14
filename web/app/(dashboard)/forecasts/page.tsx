"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Area, CartesianGrid, ComposedChart, Line, XAxis, YAxis } from "recharts";
import { CheckCircle2, PencilLine, Truck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Separator } from "@/components/ui/separator";
import { useCopilot } from "@/lib/copilot-context";
import { forecastDrugs } from "@/lib/mock-data";

const chartConfig: ChartConfig = {
  actual: { label: "Actual usage", color: "var(--chart-2)" },
  forecast: { label: "Forecasted usage", color: "var(--chart-1)" },
};

export default function ForecastsPage() {
  const { setFocus } = useCopilot();
  const [drugId, setDrugId] = useState(forecastDrugs[0].id);
  const [dispatched, setDispatched] = useState(false);
  const [editingQty, setEditingQty] = useState(false);

  const drug = useMemo(() => forecastDrugs.find((d) => d.id === drugId)!, [drugId]);
  const [quantity, setQuantity] = useState(drug.purchaseOrder.quantity);

  function selectDrug(id: string) {
    setDrugId(id);
    setDispatched(false);
    setEditingQty(false);
    const next = forecastDrugs.find((d) => d.id === id)!;
    setQuantity(next.purchaseOrder.quantity);
    setFocus({ kind: "sku", label: next.name, detail: `${next.model} forecast · ${next.confidence}% confidence` });
  }

  const chartData = drug.series.map((p) => ({
    date: p.date.slice(5),
    actual: p.actual,
    forecast: p.forecast,
    band: p.forecastLow != null && p.forecastHigh != null ? [p.forecastLow, p.forecastHigh] : undefined,
  }));

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
            <div className="flex justify-between"><span className="text-muted-foreground">Lead time</span><span>{drug.purchaseOrder.leadTimeDays} days</span></div>
            <Separator />
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Order quantity</span>
              {editingQty ? (
                <Input
                  type="number"
                  min={1}
                  value={quantity}
                  onChange={(e) => setQuantity(Math.max(1, Number(e.target.value)))}
                  className="h-7 w-24 text-right text-xs"
                  autoFocus
                  onBlur={() => setEditingQty(false)}
                />
              ) : (
                <span className="font-medium">{quantity} {drug.purchaseOrder.unit}</span>
              )}
            </div>
            <p className="text-muted-foreground">
              {quantity} {drug.purchaseOrder.unit} covers {drug.purchaseOrder.coverageDays} days of forecasted demand, including safety buffer.
            </p>
            <Separator />
            <div className="flex justify-between text-sm font-semibold"><span>Estimated total</span><span>${totalCost.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span></div>
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
