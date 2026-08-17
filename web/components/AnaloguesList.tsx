"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { StockBand, type StockStatus } from "@/components/StockBand";
import { Callout } from "@/components/dashboard/Callout";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

export type AnalogueRow = {
  rxcui: string;
  tty: string;
  name: string;
  quantity: number;
  in_stock: boolean;
  stock_status: StockStatus;
  reason?: string;
  citation?: string;
};

type AnalogueMode = "ingredient" | "full";

export function AnaloguesList({ rxcui }: { rxcui: string }) {
  const [mode, setMode] = useState<AnalogueMode>("ingredient");
  const [aiAvailable, setAiAvailable] = useState(false);
  const [aiStatusKnown, setAiStatusKnown] = useState(false);
  const [useAi, setUseAi] = useState(false);
  const [items, setItems] = useState<AnalogueRow[] | null>(null);
  const [rationaleUnavailable, setRationaleUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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

  function onMode(next: AnalogueMode) {
    setMode(next);
    setItems(null);
    setRationaleUnavailable(false);
    setError(null);
  }

  async function load(nextMode = mode, nextUseAi = useAi) {
    setBusy(true);
    setError(null);
    setItems(null);
    setRationaleUnavailable(false);
    try {
      const params = new URLSearchParams({
        mode: nextMode,
        use_ai: nextUseAi ? "true" : "false",
      });
      const body = await apiFetch(
        "analogue",
        `/analogues/${encodeURIComponent(rxcui)}?${params}`,
      );
      setItems(body.items as AnalogueRow[]);
      setRationaleUnavailable(Boolean(body.rationale_unavailable));
    } catch (err) {
      setItems(null);
      setError(err instanceof Error ? err.message : "analogues failed");
    } finally {
      setBusy(false);
    }
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
        Use AI filters the Full list; turn it off to see every candidate.
      </p>
      <Button type="button" size="sm" className="h-8 w-fit text-xs" onClick={() => void load()} disabled={busy}>
        {busy ? "Finding analogues…" : "Find analogues"}
      </Button>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      {rationaleUnavailable ? (
        <Callout tone="warning">
          Rationale unavailable. Showing the unfiltered Full list.
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
              In-stock first (High → Normal → Low), then Out of stock. Quantity is
              packs on the shelf. Nothing is selected automatically.
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Preparation</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Quantity</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((row) => (
                  <TableRow key={row.rxcui}>
                    <TableCell>
                      <p className="font-medium">{row.name}</p>
                      <p className="font-mono text-[11px] text-muted-foreground">
                        RxCUI {row.rxcui} ·{" "}
                        <Link
                          href={`/inventory?rxcui=${encodeURIComponent(row.rxcui)}&name=${encodeURIComponent(row.name)}`}
                          className="text-primary hover:underline"
                        >
                          Check inventory
                        </Link>
                      </p>
                      {row.reason ? (
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          {row.reason}
                          {row.citation ? ` “${row.citation}”` : ""}
                        </p>
                      ) : null}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{row.tty}</TableCell>
                    <TableCell className={cn("tabular-nums", !row.in_stock && "text-muted-foreground")}>
                      {row.quantity}
                    </TableCell>
                    <TableCell>
                      <StockBand status={row.stock_status} quantity={row.quantity} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )
      ) : null}
    </div>
  );
}
