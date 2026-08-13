export type StockStatus = "none" | "low" | "normal" | "high";

const LABELS: Record<StockStatus, string> = {
  none: "Out of stock",
  low: "Low",
  normal: "Normal",
  high: "High",
};

export function StockBand({
  status,
  quantity,
}: {
  status: StockStatus;
  quantity: number;
}) {
  return (
    <span className={`stock-band stock-band-${status}`}>
      {LABELS[status]} · {quantity}
    </span>
  );
}
