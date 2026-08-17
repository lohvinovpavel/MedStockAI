"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { seedOrders, type OrderStatus, type PurchaseOrder } from "@/lib/mock-data";

// The single order store. Two entry points write to it — the AI suggestion
// on Restock & Forecasts (as a draft awaiting review) and the manual form
// on Purchase & Orders (placed directly) — and /orders is the only reader
// that renders history. Session-only; nothing is persisted.
type NewOrder = Omit<PurchaseOrder, "id" | "createdAt">;

type OrdersContextValue = {
  orders: PurchaseOrder[];
  draftCount: number;
  addOrder: (order: NewOrder) => PurchaseOrder;
  updateOrderStatus: (id: string, status: OrderStatus) => void;
};

const OrdersContext = createContext<OrdersContextValue | null>(null);

export function useOrders() {
  const ctx = useContext(OrdersContext);
  if (!ctx) throw new Error("useOrders must be used within an OrdersProvider");
  return ctx;
}

// Continues the seeded PO-2026-#### sequence rather than restarting at 1,
// so newly created orders sort naturally against the existing history.
let nextRef = 149;
function nextOrderId() {
  return `PO-2026-${String(nextRef++).padStart(4, "0")}`;
}

export function OrdersProvider({ children }: { children: ReactNode }) {
  const [orders, setOrders] = useState<PurchaseOrder[]>(seedOrders);

  const addOrder = useCallback((order: NewOrder) => {
    const created: PurchaseOrder = {
      ...order,
      id: nextOrderId(),
      createdAt: new Date().toISOString().slice(0, 10),
    };
    setOrders((prev) => [created, ...prev]);
    return created;
  }, []);

  const updateOrderStatus = useCallback((id: string, status: OrderStatus) => {
    setOrders((prev) => prev.map((o) => (o.id === id ? { ...o, status } : o)));
  }, []);

  const value = useMemo(
    () => ({
      orders,
      draftCount: orders.filter((o) => o.status === "draft").length,
      addOrder,
      updateOrderStatus,
    }),
    [orders, addOrder, updateOrderStatus],
  );

  return <OrdersContext.Provider value={value}>{children}</OrdersContext.Provider>;
}
