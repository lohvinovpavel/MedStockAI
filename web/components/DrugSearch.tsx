"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { Search } from "lucide-react";
import { AnaloguesList } from "@/components/AnaloguesList";
import { StockBand, type StockStatus } from "@/components/StockBand";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

export type DrugIdentity = {
  rxcui: string;
  tty: string;
  name: string;
  strength: string | null;
  dose_form: string | null;
  in_formulary: boolean;
};

type PackageRow = { ndc: string };

type SourceStock = {
  quantity: number;
  in_stock: boolean;
  stock_status: StockStatus;
};

export function DrugSearch({ initialQuery = "" }: { initialQuery?: string }) {
  const [query, setQuery] = useState(initialQuery);
  const [items, setItems] = useState<DrugIdentity[] | null>(null);
  const [confirmed, setConfirmed] = useState<DrugIdentity | null>(null);
  const [packages, setPackages] = useState<PackageRow[] | null>(null);
  const [sourceStock, setSourceStock] = useState<SourceStock | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const runSearch = useCallback(async (raw: string) => {
    const q = raw.trim();
    if (!q) return;
    setBusy(true);
    setError(null);
    setConfirmed(null);
    setPackages(null);
    setSourceStock(null);
    try {
      const body = await apiFetch("analogue", `/drugs/search?q=${encodeURIComponent(q)}&limit=20`);
      setItems(body.items as DrugIdentity[]);
    } catch (err) {
      setItems(null);
      const message = err instanceof Error ? err.message : "search failed";
      setError(
        message === "missing credentials" || message === "invalid token"
          ? "Sign in to search preparations."
          : message,
      );
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!initialQuery.trim()) return;
    void runSearch(initialQuery);
  }, [initialQuery, runSearch]);

  function onSearch(event: FormEvent) {
    event.preventDefault();
    void runSearch(query);
  }

  async function confirmDrug(item: DrugIdentity) {
    setConfirmed(item);
    setPackages(null);
    setSourceStock(null);
    setError(null);
    try {
      const body = await apiFetch("analogue", `/drugs/${item.rxcui}/packages`);
      setPackages(body.packages as PackageRow[]);
      if (typeof body.quantity === "number" && typeof body.stock_status === "string") {
        setSourceStock({
          quantity: body.quantity as number,
          in_stock: Boolean(body.in_stock),
          stock_status: body.stock_status as StockStatus,
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "packages failed");
    }
  }

  return (
    <section className="flex flex-col gap-3">
      <form className="flex flex-wrap items-end gap-2" onSubmit={onSearch}>
        <div className="min-w-56 flex-1">
          <Label htmlFor="drug-q" className="mb-1.5 text-xs text-muted-foreground">
            Name or strength (for example Aspirin 100 mg)
          </Label>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              id="drug-q"
              type="text"
              maxLength={120}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoComplete="off"
              className="h-8 pl-8 text-xs"
              placeholder="Search preparations…"
            />
          </div>
        </div>
        <Button type="submit" size="sm" className="h-8 text-xs" disabled={busy || query.trim().length === 0}>
          {busy ? "Searching…" : "Search"}
        </Button>
      </form>
      {busy ? <Progress className="h-1 max-w-xs" /> : null}

      {error ? <p className="text-xs text-destructive">{error}</p> : null}

      {items && items.length === 0 ? (
        <div className="rounded-md border border-dashed py-8 text-center text-xs text-muted-foreground">
          Nothing found — check the spelling.
        </div>
      ) : null}

      {items && items.length > 0 ? (
        <ul className="flex flex-col gap-2">
          <p className="text-[11px] text-muted-foreground">Click a preparation to confirm it.</p>
          {items.map((item) => {
            const selected = confirmed?.rxcui === item.rxcui;
            return (
              <li key={item.rxcui} className={cn("rounded-md border bg-card", selected && "ring-1 ring-primary/40")}>
                <button
                  type="button"
                  className="flex w-full flex-col items-start gap-1 p-3 text-left"
                  aria-pressed={selected}
                  aria-expanded={selected}
                  onClick={() => void confirmDrug(item)}
                >
                  <span className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
                    {item.name}
                    {item.in_formulary ? (
                      <Badge variant="secondary" className="text-[10px] font-normal">
                        formulary
                      </Badge>
                    ) : null}
                  </span>
                  <span className="font-mono text-[11px] text-muted-foreground">
                    {item.tty} · RxCUI {item.rxcui}
                    {item.strength ? ` · ${item.strength}` : ""}
                    {item.dose_form ? ` · ${item.dose_form}` : ""}
                  </span>
                </button>
                {selected && confirmed ? (
                  <div className="flex flex-col gap-2 border-t px-3 py-3">
                    <p className="text-xs">
                      <span className="font-mono">{confirmed.rxcui}</span> · {confirmed.tty} · {confirmed.name}
                    </p>
                    {sourceStock ? (
                      <div className="flex items-center gap-2 text-xs">
                        <span className="text-muted-foreground">Shelf</span>
                        <StockBand status={sourceStock.stock_status} quantity={sourceStock.quantity} />
                      </div>
                    ) : null}
                    {packages ? (
                      <p className="text-[11px] text-muted-foreground">
                        Packages (NDC): {packages.length === 0 ? "none" : packages.length}
                        {packages.length > 0
                          ? ` — ${packages.slice(0, 5).map((p) => p.ndc).join(", ")}`
                          : ""}
                        {packages.length > 5 ? "…" : ""}
                      </p>
                    ) : null}
                    <Link
                      href={`/inventory?rxcui=${encodeURIComponent(confirmed.rxcui)}&name=${encodeURIComponent(confirmed.name)}`}
                      className="text-xs text-primary hover:underline"
                    >
                      Check inventory
                    </Link>
                    <AnaloguesList key={confirmed.rxcui} rxcui={confirmed.rxcui} />
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
