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

export function StockBand({
  status,
  quantity,
}: {
  status: StockStatus;
  quantity: number;
}) {
  return (
    <StatusBadge tone={TONE[status]}>
      {LABELS[status]} · {quantity}
    </StatusBadge>
  );
}
