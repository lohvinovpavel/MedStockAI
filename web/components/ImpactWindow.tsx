"use client";

/**
 * The patient's impact window: one body carrying the whole regimen, and the
 * per-drug breakdown behind it.
 *
 * The analogue view answers "where does THIS substitute bear?". A profile asks
 * something else — "where does everything this person is on bear, together?" —
 * and those are not the same picture. Two drugs each moderate on the liver is
 * not two moderate findings for a reader to add up; it is a liver carrying
 * both, and only the aggregate says so.
 *
 * A dialog rather than an inline panel because the figure needs vertical room
 * the cart sidebar does not have, and because opening it is a deliberate act —
 * a physician asks "show me the impact", they do not have a body permanently
 * occupying the screen while they type a drug name.
 *
 * The aggregate is computed server-side (`/cart-check` → `regimen_organs`). It
 * is a clinical claim about the patient, so it comes from the same code the
 * audit trail recorded rather than being re-derived here where it could drift.
 */

import { useState } from "react";
import { Maximize2, Minimize2 } from "lucide-react";
import { AnatomyImpact, type OrganImpact } from "@/components/AnatomyImpact";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

export type RegimenLine = {
  rxcui: string;
  name: string | null;
  verdict: string;
  organs?: OrganImpact[];
  organs_unmapped?: string[];
};

/** Worst-first, matching how the cart orders its own lines. */
const VERDICT_RANK: Record<string, number> = {
  blocked: 0,
  red: 1,
  amber: 2,
  green: 3,
};

export function ImpactWindow({
  patientName,
  regimenOrgans,
  regimenUnmapped = [],
  lines,
  disabled,
}: {
  patientName: string;
  regimenOrgans: OrganImpact[];
  regimenUnmapped?: string[];
  lines: RegimenLine[];
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  // Reading a body is a different task from glancing at one. Expanded gives the
  // figure most of the viewport and drops the legend to a column beside it, for
  // when someone is actually working through where a regimen lands rather than
  // checking whether anything is there.
  const [expanded, setExpanded] = useState(false);

  const withFindings = [...lines]
    .filter((l) => (l.organs?.length ?? 0) > 0)
    .sort(
      (a, b) =>
        (VERDICT_RANK[a.verdict] ?? 9) - (VERDICT_RANK[b.verdict] ?? 9),
    );

  // Which body to draw: the whole regimen, or one drug the reader picked out.
  const focused = focus ? lines.find((l) => l.rxcui === focus) : null;
  const shown = focused?.organs ?? regimenOrgans;
  const shownUnmapped = focused?.organs_unmapped ?? regimenUnmapped;

  const heaviest = regimenOrgans[0];

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" disabled={disabled} className="w-full">
          {/* The trigger states the headline finding rather than a bare label —
              a physician should be able to skip opening it when nothing is
              waiting inside. */}
          {regimenOrgans.length === 0
            ? "Impact — no organ findings"
            : `Impact — ${regimenOrgans.length} organ${regimenOrgans.length === 1 ? "" : "s"}${
                heaviest ? ` · ${heaviest.organ} ${heaviest.severity}` : ""
              }`}
        </Button>
      </DialogTrigger>

      <DialogContent
        className="overflow-y-auto"
        // Sized by a rule in globals.css keyed off this attribute. Utility
        // classes, tailwind-merge and an inline style all lost to
        // DialogContent's own `sm:max-w-sm`; see the note there.
        data-impact-window={expanded ? "expanded" : "normal"}
      >
        <DialogHeader>
          <div className="flex items-center justify-between gap-3 pr-8">
            <DialogTitle>Impact — {patientName}</DialogTitle>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setExpanded((v) => !v)}
              aria-label={expanded ? "Shrink the figure" : "Expand the figure"}
            >
              {expanded ? (
                <Minimize2 className="h-4 w-4" />
              ) : (
                <Maximize2 className="h-4 w-4" />
              )}
            </Button>
          </div>
          <DialogDescription>
            {focused
              ? `Showing ${focused.name ?? focused.rxcui} alone.`
              : "The whole regimen on one body. Weights sum where drugs land on the same organ; severity takes the worst, not the total."}
          </DialogDescription>
        </DialogHeader>

        {/* Switch between the aggregate and any single drug. This is the
            question the profile is actually for: whether an organ is carrying
            one drug or three. */}
        <div className="flex flex-wrap gap-1.5">
          <Button
            variant={focus === null ? "default" : "outline"}
            size="sm"
            onClick={() => setFocus(null)}
          >
            Whole regimen
          </Button>
          {withFindings.map((l) => (
            <Button
              key={l.rxcui}
              variant={focus === l.rxcui ? "default" : "outline"}
              size="sm"
              onClick={() => setFocus(l.rxcui)}
            >
              {l.name ?? l.rxcui}
            </Button>
          ))}
        </div>

        {/* Sized off the viewport when expanded so the figure grows with the
            window rather than sitting at a fixed height inside a bigger box. */}
        <AnatomyImpact
          organs={shown}
          unmapped={shownUnmapped}
          height={expanded ? 720 : 460}
        />

        {/* Which drugs put each organ where it is. The figure says WHERE; a
            physician deciding what to change needs to know WHICH, and an
            aggregate that cannot be traced back to its lines is not actionable. */}
        {focus === null && withFindings.length > 1 && (
          <div className="border-t pt-3">
            <p className="mb-2 text-sm font-medium">Contributing drugs</p>
            <ul className="space-y-1.5">
              {withFindings.map((l) => (
                <li key={l.rxcui} className="text-sm">
                  <button
                    type="button"
                    onClick={() => setFocus(l.rxcui)}
                    className="text-left hover:underline"
                  >
                    <span className="font-medium">{l.name ?? l.rxcui}</span>{" "}
                    <span className="text-muted-foreground">
                      — {(l.organs ?? []).map((o) => o.organ).join(", ")}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
