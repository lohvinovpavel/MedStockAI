"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { StockBand, type StockStatus } from "@/components/StockBand";
import { apiFetch } from "@/lib/api";

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
  const radios = `analogue-mode-${rxcui}`;

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
    <div className="analogues">
      <fieldset className="mode-row">
        <legend>Analogue search</legend>
        <label>
          <input
            type="radio"
            name={radios}
            checked={mode === "ingredient"}
            onChange={() => onMode("ingredient")}
          />
          Ingredient
        </label>
        <label>
          <input
            type="radio"
            name={radios}
            checked={mode === "full"}
            onChange={() => onMode("full")}
          />
          Full (therapeutic)
        </label>
        <label>
          <input
            type="checkbox"
            checked={useAi}
            disabled={mode !== "full" || busy || !aiAvailable}
            onChange={(event) => onUseAi(event.target.checked)}
          />
          Use AI
        </label>
        {aiStatusKnown && !aiAvailable ? (
          <span className="muted">AI is not configured</span>
        ) : null}
      </fieldset>
      <p className="muted">
        Ingredient: other strengths and brands of the same active ingredient. Full: a
        different ingredient in the same RxClass (ATC when available). Stock is attached
        automatically — in-stock first, each row with High / Normal / Low / Out of stock.
        Use AI filters the Full list; turn it off to see every candidate.
      </p>
      <button type="button" onClick={() => void load()} disabled={busy}>
        Find analogues
      </button>
      {error ? <p className="muted">{error}</p> : null}
      {rationaleUnavailable ? (
        <p className="ai-banner" role="status">
          Rationale unavailable. Showing the unfiltered Full list.
        </p>
      ) : null}
      {items ? (
        items.length === 0 ? (
          <p>No analogue options for this preparation.</p>
        ) : (
          <>
            <p className="muted">
              In-stock first (High → Normal → Low), then Out of stock. Quantity is
              packs on the shelf. Nothing is selected automatically.
            </p>
            <table className="stock-table">
              <thead>
                <tr>
                  <th>Preparation</th>
                  <th>Type</th>
                  <th>Quantity</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.rxcui}>
                    <td>
                      {row.name}
                      <div className="muted">
                        RxCUI {row.rxcui} ·{" "}
                        <Link
                          href={`/inventory?rxcui=${encodeURIComponent(row.rxcui)}&name=${encodeURIComponent(row.name)}`}
                        >
                          Check inventory
                        </Link>
                      </div>
                      {row.reason ? (
                        <div className="muted">
                          {row.reason}
                          {row.citation ? ` “${row.citation}”` : ""}
                        </div>
                      ) : null}
                    </td>
                    <td>{row.tty}</td>
                    <td className={row.in_stock ? undefined : "qty-out"}>{row.quantity}</td>
                    <td>
                      <StockBand status={row.stock_status} quantity={row.quantity} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )
      ) : null}
    </div>
  );
}
