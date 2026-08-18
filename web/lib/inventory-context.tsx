"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { apiFetch } from "@/lib/api";
import { useFacility } from "@/lib/facility-context";

export type ShelfStatus = "stockout" | "critical" | "normal" | "surplus";

export type ShelfItem = {
  ndc: string;
  name: string | null;
  facility_id: number;
  location_id: string | null;
  quantity: number;
  lot: string | null;
  earliest_expiry: string | null;
  status: ShelfStatus;
  par_defined: boolean;
  reorder_point: number | null;
  target_qty: number | null;
  suggested_qty: number | null;
  in_formulary?: boolean;
};

export type ReceivedBatch = {
  ndc: string;
  lot: string;
  quantity: number;
  expiryDate: string;
  location_id: string | null;
};

type InventoryContextValue = {
  items: ShelfItem[];
  loading: boolean;
  error: string | null;
  reload: () => void;
  receiveBatch: (batch: ReceivedBatch) => Promise<void>;
};

const InventoryContext = createContext<InventoryContextValue | null>(null);

export function useInventory() {
  const ctx = useContext(InventoryContext);
  if (!ctx) throw new Error("useInventory must be used within an InventoryProvider");
  return ctx;
}

export function InventoryProvider({ children }: { children: ReactNode }) {
  const { facility } = useFacility();
  const facilityPk = facility.id;
  const [items, setItems] = useState<ShelfItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    apiFetch("inventory", `/items?facility_id=${facilityPk}&limit=200`)
      .then((body: { items: ShelfItem[] }) => {
        if (cancelled) return;
        setItems(body.items ?? []);
        setError(null);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setItems([]);
        setError(err.message || "Cannot load inventory.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [facilityPk, tick]);

  const receiveBatch = useCallback(
    async (batch: ReceivedBatch) => {
      await apiFetch("inventory", "/batches", {
        method: "POST",
        body: JSON.stringify({
          facility_id: facilityPk,
          ndc: batch.ndc,
          lot: batch.lot,
          expiry_date: batch.expiryDate,
          quantity: batch.quantity,
          location_id: batch.location_id ?? "",
        }),
      });
      reload();
    },
    [facilityPk, reload],
  );

  const value = useMemo(
    () => ({ items, loading, error, reload, receiveBatch }),
    [items, loading, error, reload, receiveBatch],
  );

  return <InventoryContext.Provider value={value}>{children}</InventoryContext.Provider>;
}
