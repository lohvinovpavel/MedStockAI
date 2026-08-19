"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { StockBand, type StockStatus } from "@/components/StockBand";
import { Callout } from "@/components/dashboard/Callout";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiFetch } from "@/lib/api";
import { useFacility } from "@/lib/facility-context";
import { cn } from "@/lib/utils";

export type AnalogueAvailability = {
  facility_id: number;
  quantity: number;
  unit: string;
  nearest_with_stock: {
    facility_id: number;
    name: string;
    quantity: number;
    distance_km: number;
  } | null;
};

export type AnalogueRow = {
  rxcui: string;
  tty: string;
  name: string;
  quantity: number;
  in_stock: boolean;
  stock_status: StockStatus;
  reason?: string;
  citation?: string;
  availability?: AnalogueAvailability | null;
};

type AnalogueMode = "ingredient" | "full";

export function AnaloguesList({ rxcui }: { rxcui: string }) {
  const { facility } = useFacility();
  const facilityPk = facility.id;
  const [mode, setMode] = useState<AnalogueMode>("ingredient");
  const [aiAvailable, setAiAvailable] = useState(false);
  const [aiStatusKnown, setAiStatusKnown] = useState(false);
  const [useAi, setUseAi] = useState(false);
  const [items, setItems] = useState<AnalogueRow[] | null>(null);
  const [rationaleUnavailable, setRationaleUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stockDegraded, setStockDegraded] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setItems(null);
    setError(null);
    setRationaleUnavailable(false);
    setStockDegraded(false);
  }, [facilityPk]);

  useEffect(() => {
    let cancelled = false;
    apiFetch("analogue", "/analogues/ai-status")
      .then((body) => {
        if (cancelled) return;
        const available = Boolean(body.available);
        setAiAvailable(available);
        setUseAi(available);
      })
      .catch(() => {
        if (cancelled) return;
        setAiAvailable(false);
        setUseAi(false);
      })
      .finally(() => {
        if (!cancelled) setAiStatusKnown(true);
      });
    return () => {
      cancelled = true;
    };
  }, [rxcui]);

  async function load(nextMode = mode, nextUseAi = useAi) {
    setBusy(true);
    setError(null);
    setItems(null);
    setRationaleUnavailable(false);
    setStockDegraded(false);
    try {
      const params = new URLSearchParams({
        mode: nextMode,
        use_ai: nextUseAi ? "true" : "false",
        facility_id: String(facilityPk),
      });
      const body = await apiFetch(
        "analogue",
        `/analogues/${encodeURIComponent(rxcui)}?${params}`,
      );
      setItems(body.items as AnalogueRow[]);
      setRationaleUnavailable(Boolean(body.rationale_unavailable));
      setStockDegraded(Boolean(body.stock_degraded));
    } catch (err) {
      setItems(null);
      setError(err instanceof Error ? err.message : "analogues failed");
    } finally {
      setBusy(false);
    }
  }

  function onMode(next: AnalogueMode) {
    setMode(next);
    setItems(null);
    setRationaleUnavailable(false);
    setError(null);
  }

  function onUseAi(next: boolean) {
    if (!aiAvailable) return;
    setUseAi(next);
    if (mode === "full" && (items !== null || error !== null)) {
      void load(mode, next);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant={mode === "ingredient" ? "secondary" : "outline"}
          className="h-7 text-xs"
          aria-pressed={mode === "ingredient"}
          onClick={() => onMode("ingredient")}
        >
          Ingredient
        </Button>
        <Button
          type="button"
          size="sm"
          variant={mode === "full" ? "secondary" : "outline"}
          className="h-7 text-xs"
          aria-pressed={mode === "full"}
          onClick={() => onMode("full")}
        >
          Full (therapeutic)
        </Button>
        <label
          className={cn(
            "flex items-center gap-1.5 text-xs",
            mode !== "full" || busy || !aiAvailable
              ? "cursor-not-allowed text-muted-foreground/60"
              : "text-muted-foreground",
          )}
        >
          <input
            type="checkbox"
            className="size-3.5 accent-primary"
            checked={useAi}
            disabled={mode !== "full" || busy || !aiAvailable}
            onChange={(event) => onUseAi(event.target.checked)}
          />
          Use AI
        </label>
        {aiStatusKnown && !aiAvailable ? (
          <span className="text-[11px] text-muted-foreground">AI is not configured</span>
        ) : null}
      </div>
      <p className="text-[11px] text-muted-foreground">
        Ingredient: other strengths and brands of the same active ingredient. Full: a
        different ingredient in the same RxClass (ATC when available). Stock is attached
        automatically — in-stock first, each row with High / Normal / Low / Out of stock.
        Local availability is an overlay for the selected site and does not change rank.
        Use AI works only in Full mode and keeps up to 5 substitutes with a
        reason; turn it off to see every candidate. Ingredient never calls AI.
      </p>
      <div className="flex flex-col gap-1.5">
        <Button type="button" size="sm" className="h-8 w-fit text-xs" onClick={() => void load()} disabled={busy}>
          {busy ? "Finding analogues…" : "Find analogues"}
        </Button>
        {busy ? <Progress className="h-1 w-48" /> : null}
      </div>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      {stockDegraded ? (
        <Callout tone="warning">
          Shelf quantities are unavailable, so this list is ranked without local
          availability. Try again after inventory is reachable.
        </Callout>
      ) : null}
      {rationaleUnavailable ? (
        <Callout tone="warning">
          Use AI could not filter this list, so this is every Full candidate — not
          the top 5. Turn Use AI off for the same unfiltered list, or try again.
        </Callout>
      ) : null}
      {items && !rationaleUnavailable && mode === "full" && useAi ? (
        <Callout tone="normal">
          AI kept {items.length} substitute{items.length === 1 ? "" : "s"} (max 5).
          Each row should include a short reason.
        </Callout>
      ) : null}
      {items ? (
        items.length === 0 ? (
          <div className="rounded-md border border-dashed py-8 text-center text-xs text-muted-foreground">
            No analogue options for this preparation.
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg border bg-card">
            <p className="border-b px-3 py-2 text-[11px] text-muted-foreground">
              {items.length} row{items.length === 1 ? "" : "s"}. Rank is hospital
              quantity (High → Normal → Low, then Out of stock). The Here column
              is stock at {facility.name} and does not change that order.
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Preparation</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Here</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((row) => {
                  const here = row.availability;
                  const nearest = here?.nearest_with_stock;
                  const hereQty = here?.quantity ?? row.quantity;
                  const notHere = here != null && here.quantity <= 0;
                  return (
                  <TableRow key={row.rxcui}>
                    <TableCell>
                      <p className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{row.name}</span>
                        <span className="flex items-center gap-1.5">
                          <span className="text-xs font-normal text-muted-foreground">Shelf</span>
                          <StockBand status={row.stock_status} quantity={row.quantity} />
                        </span>
                      </p>
                      <p className="font-mono text-[11px] text-muted-foreground">
                        RxCUI {row.rxcui} ·{" "}
                        <Link
                          href={`/inventory?rxcui=${encodeURIComponent(row.rxcui)}&name=${encodeURIComponent(row.name)}`}
                          className="text-primary hover:underline"
                        >
                          Check inventory
                        </Link>
                      </p>
                      {nearest ? (
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          Nearest with stock: {nearest.name} · {nearest.distance_km} km · {nearest.quantity} {here?.unit ?? "packs"}
                        </p>
                      ) : null}
                      {row.reason ? (
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          {row.reason}
                          {row.citation ? ` “${row.citation}”` : ""}
                        </p>
                      ) : null}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{row.tty}</TableCell>
                    <TableCell className={cn("tabular-nums", notHere && "text-muted-foreground")}>
                      {here == null
                        ? row.quantity
                        : notHere
                          ? "Not stocked here"
                          : `${hereQty} ${here.unit} here`}
                    </TableCell>
                  </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )
      ) : null}
    </div>
  );
}
