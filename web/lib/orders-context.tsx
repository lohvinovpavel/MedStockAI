"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { apiFetch } from "@/lib/api";

export type OrderStatus = "draft" | "placed" | "in_transit" | "delivered" | "cancelled";
export type OrderSource = "ai_suggestion" | "manual";

export type OrderListItem = {
  id: number;
  ref: string;
  created_at: string | null;
  facility: { id: number; code: string | null; name: string | null };
  supplier: { id: number; name: string | null };
  status: OrderStatus;
  source: OrderSource;
  line_count: number;
  primary_drug: string | null;
  quantity: number;
  total: number;
  shipping: number;
  expected_delivery: string | null;
  note: string | null;
};

export type OrderSummary = {
  drafts_awaiting_review: number;
  in_transit: number;
  delivered_this_month: number;
  timezone: string;
  committed_spend: { amount: number; currency: string; definition: string };
};

type CreateOrderInput = {
  facility_id: number;
  supplier_id: number;
  status: "draft" | "placed";
  source: OrderSource;
  review_decision_id?: number | null;
  lines: { ndc: string; quantity: number }[];
  note?: string | null;
};

type OrdersContextValue = {
  orders: OrderListItem[];
  summary: OrderSummary | null;
  draftCount: number;
  loading: boolean;
  reload: () => void;
  createOrder: (input: CreateOrderInput) => Promise<OrderListItem>;
  placeOrder: (id: number) => Promise<void>;
  discardDraft: (id: number) => Promise<void>;
};

const OrdersContext = createContext<OrdersContextValue | null>(null);

export function useOrders() {
  const ctx = useContext(OrdersContext);
  if (!ctx) throw new Error("useOrders must be used within an OrdersProvider");
  return ctx;
}

const EMPTY_SUMMARY: OrderSummary = {
  drafts_awaiting_review: 0,
  in_transit: 0,
  delivered_this_month: 0,
  timezone: "UTC",
  committed_spend: { amount: 0, currency: "USD", definition: "" },
};

export function OrdersProvider({ children }: { children: ReactNode }) {
  const [orders, setOrders] = useState<OrderListItem[]>([]);
  const [summary, setSummary] = useState<OrderSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      apiFetch("inventory", "/orders?limit=200") as Promise<{ items: OrderListItem[] }>,
      apiFetch("inventory", "/orders/summary") as Promise<OrderSummary>,
    ])
      .then(([list, sum]) => {
        if (cancelled) return;
        setOrders(list.items ?? []);
        setSummary(sum);
      })
      .catch(() => {
        if (cancelled) return;
        setOrders([]);
        setSummary(EMPTY_SUMMARY);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const createOrder = useCallback(async (input: CreateOrderInput) => {
    const created = (await apiFetch("inventory", "/orders", {
      method: "POST",
      body: JSON.stringify(input),
    })) as OrderListItem;
    reload();
    return created;
  }, [reload]);

  const placeOrder = useCallback(async (id: number) => {
    await apiFetch("inventory", `/orders/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status: "placed" }),
    });
    reload();
  }, [reload]);

  const discardDraft = useCallback(async (id: number) => {
    await apiFetch("inventory", `/orders/${id}`, { method: "DELETE" });
    reload();
  }, [reload]);

  const value = useMemo(
    () => ({
      orders,
      summary,
      draftCount: summary?.drafts_awaiting_review ?? orders.filter((o) => o.status === "draft").length,
      loading,
      reload,
      createOrder,
      placeOrder,
      discardDraft,
    }),
    [orders, summary, loading, reload, createOrder, placeOrder, discardDraft],
  );

  return <OrdersContext.Provider value={value}>{children}</OrdersContext.Provider>;
}
