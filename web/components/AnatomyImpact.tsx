"use client";

/**
 * The organs a drug bears on for this patient, marked on an anatomical figure.
 *
 * Same contract as the schematic BodyImpact it replaces: the shading comes from
 * `result.organs`, computed server-side in `medstock_shared/organs.py` from the
 * findings that actually fired. The component never infers an organ from a drug
 * name. Warfarin does not mark a liver because warfarin is hepatically cleared;
 * it marks one when THIS patient's assessment raised a hepatic finding.
 *
 * Why a photograph with an overlay rather than drawn shapes: eight passes of
 * hand-authored SVG got to "clean schematic" and stopped. The failure mode was
 * always the same — severity carried by fill, and fill area dominated by organ
 * size, so a moderate lung outshouted a high kidney because it was ten times
 * the area. Here severity is a **ring** at a fixed radius per organ, which is
 * independent of how large the organ looks in the artwork. A high kidney reads
 * as urgent whether or not it is small.
 *
 * The figure is anatomical context, not a picture of the patient. It is chosen
 * by recorded sex and captioned when that is unknown.
 */

import { useState } from "react";
import {
  ANATOMY_CREDIT,
  ORGAN_RADIUS,
  templateFor,
  type AnatomyTemplate,
} from "@/lib/anatomy";

export type OrganImpact = {
  organ: string;
  severity: "block" | "high" | "moderate" | "low";
  weight: number;
  reasons: string[];
};

/** Ring colour and legend styling per severity. The ring does the work on the
 *  figure; the fill is a light wash that only tints what is already there. */
const SEVERITY = {
  block: { ring: "#B91C1C", wash: "#DC2626", label: "Blocked", badge: "bg-red-100 text-red-900 border-red-300" },
  high: { ring: "#DC2626", wash: "#EF4444", label: "High", badge: "bg-red-100 text-red-900 border-red-300" },
  moderate: { ring: "#D97706", wash: "#F59E0B", label: "Moderate", badge: "bg-amber-100 text-amber-900 border-amber-300" },
  low: { ring: "#CA8A04", wash: "#FDE047", label: "Low", badge: "bg-yellow-100 text-yellow-900 border-yellow-300" },
} as const;

const ORGAN_LABEL: Record<string, string> = {
  brain: "Brain",
  oesophagus: "Oesophagus",
  lungs: "Lungs",
  heart: "Heart",
  liver: "Liver",
  gallbladder: "Gallbladder",
  stomach: "Stomach",
  spleen: "Spleen",
  pancreas: "Pancreas",
  kidneys: "Kidneys",
  intestines: "Intestines",
  bladder: "Bladder",
  blood: "Circulatory",
  thyroid: "Thyroid",
  skin: "Skin",
};

function Figure({
  template,
  organs,
  hovered,
  setHovered,
  height,
}: {
  template: AnatomyTemplate;
  organs: OrganImpact[];
  hovered: string | null;
  setHovered: (o: string | null) => void;
  height: number;
}) {
  const [w, h] = template.viewBox;
  const marks = organs.filter((o) => template.anchors[o.organ]);

  return (
    <div
      className="relative shrink-0"
      // The template's own proportions, held by CSS rather than a computed
      // pixel width -- so the figure fits whatever box it is given and never
      // distorts. maxWidth stops a tall container stretching it past the
      // column it sits in.
      style={{ height, aspectRatio: `${w} / ${h}`, maxWidth: "100%" }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={template.src}
        alt={`${template.label} anatomical figure`}
        className="absolute inset-0 h-full w-full select-none object-contain"
        draggable={false}
      />
      {/* The overlay shares the template's viewBox, so anchors are used as-is —
          no scaling maths, and nothing to drift when the figure is resized. */}
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className="absolute inset-0 h-full w-full"
        role="img"
        aria-label={
          marks.length
            ? `Affected: ${marks.map((m) => `${m.organ} ${m.severity}`).join(", ")}`
            : "No organ-specific findings"
        }
      >
        {marks.map((m) => {
          const [x, y] = template.anchors[m.organ]!;
          const r = ORGAN_RADIUS[m.organ] ?? 60;
          const s = SEVERITY[m.severity];
          const dim = hovered !== null && hovered !== m.organ;
          return (
            <g
              key={m.organ}
              opacity={dim ? 0.25 : 1}
              className="transition-opacity"
              onMouseEnter={() => setHovered(m.organ)}
              onMouseLeave={() => setHovered(null)}
              style={{ cursor: "pointer" }}
            >
              <circle cx={x} cy={y} r={r} fill={s.wash} fillOpacity={0.28} />
              {/* The ring is the signal. Fixed weight per severity, so a small
                  organ with a high finding is as loud as a large one. */}
              <circle
                cx={x}
                cy={y}
                r={r}
                fill="none"
                stroke={s.ring}
                strokeWidth={m.severity === "low" ? 5 : 8}
                strokeOpacity={0.95}
              />
              {hovered === m.organ && (
                <circle cx={x} cy={y} r={r + 12} fill="none" stroke={s.ring} strokeWidth={3} strokeOpacity={0.5} />
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function AnatomyImpact({
  organs,
  unmapped = [],
  sex,
  title,
  height = 420,
  dense = false,
}: {
  organs: OrganImpact[];
  unmapped?: string[];
  /** "F" | "M"; anything else draws a frame and says the sex is unrecorded. */
  sex?: string | null;
  title?: string;
  height?: number;
  /** Card mode: figure + chips only. The reasons live in the window this
   *  summary sits above, so repeating them here would crowd a sidebar and say
   *  nothing new. */
  dense?: boolean;
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  const { template, known } = templateFor(sex);

  // Organs the mapping produced but this template has no anchor for. Surfaced
  // rather than dropped, for the same reason `unmapped` is: a reader counts
  // what is marked and believes they have seen everything.
  const unplaced = organs.filter((o) => !template.anchors[o.organ]).map((o) => o.organ);

  if (dense) {
    return (
      <div className="flex flex-col gap-1.5">
        <p className="text-[11px] font-medium text-slate-600 dark:text-slate-300">
          Where this regimen bears
        </p>
        <div className="flex items-start gap-3">
        <Figure
          template={template}
          organs={organs}
          hovered={hovered}
          setHovered={setHovered}
          height={height}
        />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          {organs.length === 0 ? (
            <span className="text-[11px] text-muted-foreground">
              Nothing in this regimen bears on a specific organ.
            </span>
          ) : (
            organs.map((o) => {
              const sev = SEVERITY[o.severity];
              return (
                <span
                  key={o.organ}
                  onMouseEnter={() => setHovered(o.organ)}
                  onMouseLeave={() => setHovered(null)}
                  className={`truncate rounded border px-1.5 py-0.5 text-[10px] ${sev.badge}`}
                >
                  {ORGAN_LABEL[o.organ] ?? o.organ} — {sev.label} risk · {o.weight} pts
                </span>
              );
            })
          )}
          {unmapped.length > 0 && (
            <span className="text-[10px] text-muted-foreground">
              {unmapped.length} further finding{unmapped.length === 1 ? "" : "s"} not
              specific to any organ — see the full impact.
            </span>
          )}
        </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {title ? (
        <h4 className="text-sm font-medium text-slate-700 dark:text-slate-200">{title}</h4>
      ) : null}

      <div className="flex flex-wrap items-start gap-5">
        <Figure
          template={template}
          organs={organs}
          hovered={hovered}
          setHovered={setHovered}
          height={height}
        />

        <div className="min-w-0 flex-1 space-y-2">
          {organs.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400">
              No organ-specific findings for this patient.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {organs.map((o) => {
                const s = SEVERITY[o.severity];
                return (
                  <li
                    key={o.organ}
                    onMouseEnter={() => setHovered(o.organ)}
                    onMouseLeave={() => setHovered(null)}
                    className={[
                      "rounded-md border px-2.5 py-1.5 text-sm transition-colors",
                      hovered === o.organ
                        ? "border-slate-400 bg-slate-50 dark:border-slate-500 dark:bg-slate-800"
                        : "border-slate-200 dark:border-slate-700",
                    ].join(" ")}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-slate-800 dark:text-slate-100">
                        {ORGAN_LABEL[o.organ] ?? o.organ}
                      </span>
                      <span className={`rounded border px-1.5 py-0.5 text-xs ${s.badge}`}>
                        {s.label} risk · {o.weight} pts
                      </span>
                    </div>
                    {/* The findings themselves. A mark a reader cannot trace to
                        a reason is a mark they have to take on trust. */}
                    <ul className="mt-1 space-y-0.5">
                      {o.reasons.map((r, i) => (
                        <li key={i} className="text-xs text-slate-600 dark:text-slate-300">
                          {r}
                        </li>
                      ))}
                    </ul>
                  </li>
                );
              })}
            </ul>
          )}

          {unmapped.length > 0 && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Not marked on the figure:{" "}
              {unmapped.join(", ").toLowerCase().replace(/_/g, " ")} — not specific
              to one organ.
            </p>
          )}
          {unplaced.length > 0 && (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              No position on this figure for: {unplaced.join(", ")}. Listed above
              but not marked.
            </p>
          )}
          {!known && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Sex not recorded; showing the {template.label} figure as anatomical
              context only.
            </p>
          )}
          <p className="pt-1 text-[11px] text-slate-400 dark:text-slate-500">
            Figure: {ANATOMY_CREDIT.author} via {ANATOMY_CREDIT.source} ·{" "}
            {ANATOMY_CREDIT.license}
          </p>
        </div>
      </div>
    </div>
  );
}
