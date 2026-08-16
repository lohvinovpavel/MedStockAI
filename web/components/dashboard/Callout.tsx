import { cn } from "@/lib/utils";
import { TONE_STYLE, type StatusTone } from "./StatusBadge";

// Shared alert/confirmation box. Reuses StatusBadge's tone→color map so a
// "critical" banner looks the same as a "critical" badge everywhere — before
// this, each page hand-rolled its own tint and dark-mode convention
// (opaque -950 shade in one place, alpha tint in another) for the same
// meaning.
export function Callout({
  tone,
  className,
  children,
}: {
  tone: Extract<StatusTone, "critical" | "warning" | "normal">;
  className?: string;
  children: React.ReactNode;
}) {
  return <div className={cn("rounded-md border p-2.5 text-xs", TONE_STYLE[tone], className)}>{children}</div>;
}
