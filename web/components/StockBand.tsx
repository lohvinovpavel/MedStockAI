import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";

export type StockStatus = "none" | "low" | "normal" | "high";

const LABELS: Record<StockStatus, string> = {
  none: "Out of stock",
  low: "Low",
  normal: "Normal",
  high: "High",
};

const TONE: Record<StockStatus, StatusTone> = {
  none: "stockout",
  low: "warning",
  normal: "normal",
  high: "surplus",
};

export function coerceStockStatus(value: string | undefined | null): StockStatus {
  if (value === "none" || value === "low" || value === "normal" || value === "high") {
    return value;
  }
  return "none";
}

export function StockBand({
  status,
  quantity,
}: {
  status: StockStatus | string | undefined | null;
  quantity: number | undefined | null;
}) {
  const resolved = coerceStockStatus(status);
  const qty = typeof quantity === "number" && Number.isFinite(quantity) ? quantity : 0;
  return (
    <StatusBadge tone={TONE[resolved]}>
      {LABELS[resolved]} · {qty}
    </StatusBadge>
  );
}
