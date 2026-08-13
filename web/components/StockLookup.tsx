"use client";

import { FormEvent, Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AnaloguesList } from "@/components/AnaloguesList";
import { apiFetch } from "@/lib/api";

type StockItem = { ndc: string; quantity: number; location_id: string | null };

function StockLookupInner() {
  const searchParams = useSearchParams();
  const initial = searchParams.get("rxcui") ?? "";
  const nameFromQuery = searchParams.get("name");
  const [rxcui, setRxcui] = useState(initial);
  const [lookedUp, setLookedUp] = useState<string | null>(null);
  const [ndcCount, setNdcCount] = useState<number | null>(null);
  const [items, setItems] = useState<StockItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load(id: string) {
    const q = id.trim();
    if (!q) return;
    setBusy(true);
    setError(null);
    try {
      const body = await apiFetch("inventory", `/stock?rxcui=${encodeURIComponent(q)}`);
      setLookedUp(body.rxcui as string);
      setNdcCount(body.ndc_count as number);
      setItems(body.items as StockItem[]);
    } catch (err) {
      setItems(null);
      setLookedUp(null);
      setNdcCount(null);
      setError(err instanceof Error ? err.message : "stock lookup failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const q = initial.trim();
    if (!q) return;
    let cancelled = false;
    setBusy(true);
    setError(null);
    apiFetch("inventory", `/stock?rxcui=${encodeURIComponent(q)}`)
      .then((body) => {
        if (cancelled) return;
        setLookedUp(body.rxcui as string);
        setNdcCount(body.ndc_count as number);
        setItems(body.items as StockItem[]);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setItems(null);
        setLookedUp(null);
        setNdcCount(null);
        setError(err instanceof Error ? err.message : "stock lookup failed");
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [initial]);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void load(rxcui);
  }

  const emptyShelf = items !== null && (ndcCount ?? 0) > 0 && items.length === 0;
  const missingDrug = items !== null && (ndcCount ?? 0) === 0;

  return (
    <section>
      <form className="row" onSubmit={onSubmit}>
        <div>
          <label htmlFor="stock-rxcui">RxCUI</label>
          <input
            id="stock-rxcui"
            type="text"
            value={rxcui}
            onChange={(e) => setRxcui(e.target.value)}
            autoComplete="off"
          />
        </div>
        <button type="submit" disabled={busy || rxcui.trim().length === 0}>
          Check stock
        </button>
      </form>
      {error ? <p className="muted">{error}</p> : null}
      {items ? (
        <div className="identity">
          <h2>Stock</h2>
          <p>
            RxCUI <code>{lookedUp}</code>
            {nameFromQuery && lookedUp === initial.trim() ? ` · ${nameFromQuery}` : ""}
          </p>
          {missingDrug ? (
            <p className="muted">No packages for this RxCUI — unknown, or RxNorm has no NDC packs.</p>
          ) : null}
          {emptyShelf ? (
            <p className="muted">None on the shelf — find analogues ranked by what is in stock.</p>
          ) : null}
          {items.length > 0 ? (
            <>
              <p className="muted">
                {ndcCount ?? 0} NDCs from RxNorm — {items.length} line(s)
              </p>
              <table className="stock-table">
                <thead>
                  <tr>
                    <th>NDC</th>
                    <th>Location</th>
                    <th>Quantity</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((row) => (
                    <tr key={`${row.ndc}-${row.location_id ?? ""}`}>
                      <td>
                        <code>{row.ndc}</code>
                      </td>
                      <td>{row.location_id || "—"}</td>
                      <td className={row.quantity === 0 ? "qty-out" : undefined}>
                        {row.quantity}
                        {row.quantity === 0 ? " — out" : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
          {lookedUp ? <AnaloguesList key={lookedUp} rxcui={lookedUp} /> : null}
        </div>
      ) : null}
    </section>
  );
}

export function StockLookup() {
  return (
    <Suspense fallback={<p className="muted">…</p>}>
      <StockLookupInner />
    </Suspense>
  );
}
