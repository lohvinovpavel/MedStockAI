"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { inventoryFor, type InventoryItem } from "@/lib/mock-data";

// Session-only overlay on top of the canonical `inventory` array —
// inventoryFor() itself stays a pure function (every other page still calls
// it directly and is unaffected); this composes on top of it for the one
// page that needs to write. "Receive Batch" used to validate a full form
// and then discard it; this makes the write real for the table and KPIs on
// this page. Scoped deliberately: other pages (Forecasts, Orders, Audit,
// Shortages) don't read through this overlay, so a batch received here
// doesn't yet ripple into a forecast's stock baseline or a shortage row —
// a real limitation, not a currently-solved one.
export interface ReceivedBatch {
  itemId?: string; // existing SKU being topped up; omitted for a new SKU
  drugName: string;
  batchNumber: string;
  quantity: number;
  expiryDate: string;
}

type InventoryContextValue = {
  itemsFor: (facilityId: string) => InventoryItem[];
  receiveBatch: (facilityId: string, batch: ReceivedBatch) => InventoryItem;
};

const InventoryContext = createContext<InventoryContextValue | null>(null);

export function useInventory() {
  const ctx = useContext(InventoryContext);
  if (!ctx) throw new Error("useInventory must be used within an InventoryProvider");
  return ctx;
}

let nextNewItemId = 1;

interface TopUp {
  extraStock: number;
  batchNumber: string; // the most recent batch received wins the display
}

export function InventoryProvider({ children }: { children: ReactNode }) {
  // facilityId -> itemId -> accumulated top-up for this session
  const [topUps, setTopUps] = useState<Record<string, Record<string, TopUp>>>({});
  // facilityId -> items received that aren't in the base catalogue
  const [newItems, setNewItems] = useState<Record<string, InventoryItem[]>>({});

  const receiveBatch = useCallback((facilityId: string, batch: ReceivedBatch) => {
    if (batch.itemId) {
      const itemId = batch.itemId;
      setTopUps((prev) => ({
        ...prev,
        [facilityId]: {
          ...prev[facilityId],
          [itemId]: {
            extraStock: (prev[facilityId]?.[itemId]?.extraStock ?? 0) + batch.quantity,
            batchNumber: batch.batchNumber,
          },
        },
      }));
      const existing = inventoryFor(facilityId).find((i) => i.id === itemId)!;
      return { ...existing, currentStock: existing.currentStock + batch.quantity, batchNumber: batch.batchNumber };
    }

    const created: InventoryItem = {
      id: `rb-${nextNewItemId++}`,
      facilityId,
      drugName: batch.drugName,
      form: "Received batch",
      inn: "—",
      atcCode: "—",
      // Deliberately empty. Someone typing a free-text drug name has given us
      // nothing to certify, so the badge reads "unknown" rather than assuming
      // the best — that gap is precisely what COMP-2 exploration is for.
      ndc: "",
      batchNumber: batch.batchNumber,
      currentStock: batch.quantity,
      unit: "units",
      dailyBurnRate: 1,
      expiryDate: batch.expiryDate,
      certStatus: "pending",
      certAuthority: "FDA",
      certNumber: "Pending review",
      analogues: [],
    };
    setNewItems((prev) => ({ ...prev, [facilityId]: [...(prev[facilityId] ?? []), created] }));
    return created;
  }, []);

  const itemsFor = useCallback(
    (facilityId: string) => {
      const base = inventoryFor(facilityId).map((item) => {
        const topUp = topUps[facilityId]?.[item.id];
        return topUp ? { ...item, currentStock: item.currentStock + topUp.extraStock, batchNumber: topUp.batchNumber } : item;
      });
      return [...base, ...(newItems[facilityId] ?? [])];
    },
    [topUps, newItems],
  );

  const value = useMemo(() => ({ itemsFor, receiveBatch }), [itemsFor, receiveBatch]);

  return <InventoryContext.Provider value={value}>{children}</InventoryContext.Provider>;
}
