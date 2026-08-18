"use client";

import { CheckCircle2, Clock, XCircle } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { EMPTY_COUNTS, type QueueCounts, type ProfileStatus } from "@/lib/prognosis";
import { cn } from "@/lib/utils";

/**
 * Two pictures of one thing: where every extracted risk profile currently sits,
 * and whether the extraction behind them is good enough to trust.
 *
 * Colour here is a **status** palette, not a categorical one — the three slots
 * are states, not identities. Validated with the data-viz palette checks against
 * both surfaces (white / #171717): adjacent CVD ΔE 8.9 light and 8.7 dark, both
 * over the 8.0 target, and the normal-vision floor clears at 21.2 / 15.6.
 *
 * `rejected` is deliberately a neutral slate rather than a red. It fails the
 * chroma floor on purpose: that check exists to stop a hue reading as gray when
 * hue carries identity, and here gray *is* the meaning — a rejected profile is
 * closed and not served, which is the gate working, not an incident. Identity
 * never rests on the colour anyway; every slot ships with an icon and a label.
 */

type Slot = { key: ProfileStatus; label: string; fill: string; icon: typeof Clock };

// Order is the life of a profile: extracted → ruled on. Not sorted by size —
// a bar whose segments reorder as counts change is unreadable across refreshes.
// One fill per slot, shared by the segment and its legend swatch, so the two
// can never drift into showing different colours for the same state.
const SLOTS: Slot[] = [
  { key: "awaiting_approval", label: "Awaiting review", fill: "bg-[#f59e0b] dark:bg-[#d97706]", icon: Clock },
  { key: "approved", label: "Approved", fill: "bg-[#10b981] dark:bg-[#0d9e6e]", icon: CheckCircle2 },
  { key: "rejected", label: "Rejected", fill: "bg-[#64748b]", icon: XCircle },
];

/** docs/prognosis-and-procurement.md §5.4 — below this, fix the prompt first. */
const TARGET = 0.8;

/**
 * Part-to-whole across three states. No labels inside the segments: one status
 * routinely holds everything and the others nothing, so an in-segment label
 * would be clipped exactly when the queue is most lopsided. The legend below
 * carries icon, label and count for every slot including the empty ones — a
 * zero that disappears from the bar must not disappear from the reading.
 */
function GateBar({ counts }: { counts: QueueCounts }) {
  const total = SLOTS.reduce((sum, s) => sum + (counts[s.key] ?? 0), 0);
  const present = SLOTS.filter((s) => (counts[s.key] ?? 0) > 0);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex h-2.5 gap-0.5 overflow-hidden" role="img" aria-label={
        total === 0
          ? "No risk profiles extracted yet"
          : SLOTS.map((s) => `${counts[s.key] ?? 0} ${s.label.toLowerCase()}`).join(", ")
      }>
        {total === 0 ? (
          <div className="flex-1 rounded-[4px] bg-muted" />
        ) : (
          present.map((slot, i) => (
            <div
              key={slot.key}
              className={cn(
                slot.fill,
                i === 0 && "rounded-l-[4px]",
                i === present.length - 1 && "rounded-r-[4px]",
              )}
              style={{ flexBasis: `${((counts[slot.key] ?? 0) / total) * 100}%` }}
            />
          ))
        )}
      </div>

      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {SLOTS.map((slot) => {
          const Icon = slot.icon;
          const n = counts[slot.key] ?? 0;
          return (
            <li key={slot.key} className="flex items-center gap-1.5 text-xs">
              <span className={cn("size-2 shrink-0 rounded-[2px]", n > 0 ? slot.fill : "bg-muted-foreground/25")} />
              <Icon className="size-3.5 text-muted-foreground" aria-hidden />
              <span className="text-muted-foreground">{slot.label}</span>
              <span className="font-mono font-semibold tabular-nums">{n}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * A single ratio against a limit, so: a meter, not a chart.
 *
 * `null` is not zero and must not render as a bar at the far left. Nobody having
 * ruled on anything is the state this project is actually in, and drawing it as
 * 0% would read as "the model gets everything wrong" against a threshold it was
 * never measured on.
 */
function AcceptRate({ rate, ruled }: { rate: number | null; ruled: number }) {
  const pct = rate === null ? null : Math.round(rate * 100);
  const belowTarget = rate !== null && rate < TARGET;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        {/* Hero figure: proportional digits, not tabular — at this size
            equal-width digits read loose. */}
        <span className="text-5xl font-semibold leading-none tracking-tight">
          {pct === null ? "—" : `${pct}%`}
        </span>
        <span className="text-xs text-muted-foreground">
          {pct === null ? "not yet measured" : `of ${ruled} ruled on`}
        </span>
      </div>

      <div className="relative h-2.5 w-full rounded-[4px] bg-muted" aria-hidden>
        {pct !== null && (
          <div
            className={cn(
              "h-full rounded-[4px]",
              belowTarget ? "bg-[#f59e0b] dark:bg-[#d97706]" : "bg-[#10b981] dark:bg-[#0d9e6e]",
            )}
            style={{ width: `${pct}%` }}
          />
        )}
        {/* The §5.4 line. A real threshold, so it is drawn — unlike a grid, which
            would be noise. */}
        <span
          className="absolute top-[-3px] h-[calc(100%+6px)] w-0.5 rounded-full bg-foreground/70"
          style={{ left: `${TARGET * 100}%` }}
        />
      </div>

      <p className="text-[11px] text-muted-foreground">
        Share of ruled-on profiles a pharmacist accepted, against the {TARGET * 100}% line at which
        extraction is worth trusting. Profiles still awaiting review are excluded — counting them
        would measure the reviewer&apos;s progress, not the model&apos;s accuracy.
      </p>
    </div>
  );
}

export function ReviewGate({
  counts = EMPTY_COUNTS,
  acceptRate,
  muted,
}: {
  counts?: QueueCounts;
  acceptRate: number | null;
  /** Held at reduced opacity while a refetch is in flight — no skeleton flash. */
  muted?: boolean;
}) {
  const ruled = (counts.approved ?? 0) + (counts.rejected ?? 0);

  return (
    <div className={cn("grid gap-3 lg:grid-cols-2", muted && "opacity-60 transition-opacity")}>
      <Card className="gap-3 py-4">
        <CardContent className="flex flex-col gap-3 px-4">
          <div>
            <p className="text-sm font-medium">The gate</p>
            <p className="text-xs text-muted-foreground">
              Every extracted profile, and which side of the approval gate it sits on.
            </p>
          </div>
          <GateBar counts={counts} />
        </CardContent>
      </Card>

      <Card className="gap-3 py-4">
        <CardContent className="flex flex-col gap-3 px-4">
          <div>
            <p className="text-sm font-medium">Accept rate</p>
            <p className="text-xs text-muted-foreground">Is the extraction good enough to trust?</p>
          </div>
          <AcceptRate rate={acceptRate} ruled={ruled} />
        </CardContent>
      </Card>
    </div>
  );
}
