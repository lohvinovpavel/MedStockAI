"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { AnaloguesList } from "@/components/AnaloguesList";
import { StockBand, type StockStatus } from "@/components/StockBand";
import { apiFetch } from "@/lib/api";

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

export function DrugSearch() {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<DrugIdentity[] | null>(null);
  const [confirmed, setConfirmed] = useState<DrugIdentity | null>(null);
  const [packages, setPackages] = useState<PackageRow[] | null>(null);
  const [sourceStock, setSourceStock] = useState<SourceStock | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSearch(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
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
      setError(err instanceof Error ? err.message : "search failed");
    } finally {
      setBusy(false);
    }
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
    <section>
      <form className="row" onSubmit={onSearch}>
        <div>
          <label htmlFor="drug-q">Name or strength (for example Aspirin 100 mg)</label>
          <input
            id="drug-q"
            type="text"
            maxLength={120}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoComplete="off"
          />
        </div>
        <button type="submit" disabled={busy || query.trim().length === 0}>
          Search
        </button>
      </form>

      {error ? <p className="muted">{error}</p> : null}

      {items && items.length === 0 ? (
        <p>Nothing found — check the spelling.</p>
      ) : null}

      {items && items.length > 0 ? (
        <>
          <p className="muted">Click a preparation to confirm it.</p>
          <ul className="result-list">
            {items.map((item) => {
              const selected = confirmed?.rxcui === item.rxcui;
              return (
                <li key={item.rxcui} className={selected ? "expanded" : undefined}>
                  <button
                    type="button"
                    className="pick"
                    aria-pressed={selected}
                    aria-expanded={selected}
                    onClick={() => void confirmDrug(item)}
                  >
                    <strong>{item.name}</strong>
                    {item.in_formulary ? <span className="badge">formulary</span> : null}
                    <div className="muted">
                      {item.tty} · RxCUI {item.rxcui}
                      {item.strength ? ` · ${item.strength}` : ""}
                      {item.dose_form ? ` · ${item.dose_form}` : ""}
                    </div>
                  </button>
                  {selected && confirmed ? (
                    <div className="identity">
                      <h2>Selected drug</h2>
                      <p>
                        <code>{confirmed.rxcui}</code> · {confirmed.tty} · {confirmed.name}
                      </p>
                      {sourceStock ? (
                        <p>
                          Shelf:{" "}
                          <StockBand
                            status={sourceStock.stock_status}
                            quantity={sourceStock.quantity}
                          />
                        </p>
                      ) : null}
                      {packages ? (
                        <p className="muted">
                          Packages (NDC): {packages.length === 0 ? "none" : packages.length}
                          {packages.length > 0
                            ? ` — ${packages.slice(0, 5).map((p) => p.ndc).join(", ")}`
                            : ""}
                          {packages.length > 5 ? "…" : ""}
                        </p>
                      ) : null}
                      <p>
                        <Link
                          href={`/inventory?rxcui=${encodeURIComponent(confirmed.rxcui)}&name=${encodeURIComponent(confirmed.name)}`}
                        >
                          Check inventory
                        </Link>
                      </p>
                      <AnaloguesList key={confirmed.rxcui} rxcui={confirmed.rxcui} />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
    </section>
  );
}
