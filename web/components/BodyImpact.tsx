"use client";

/**
 * A body, with the organs this drug bears on for this patient shaded.
 *
 * The assessment already returns findings with codes and weights. A physician
 * reading ten codes has to hold an anatomy lesson in their head to see the
 * shape of the risk; the same ten codes on a torso say "kidneys and liver" in
 * one glance. That is the whole claim — this shows what the ruleset already
 * decided, it does not decide anything.
 *
 * Which is why the shading comes from `result.organs`, computed server-side in
 * `medstock_shared/organs.py` from the findings that actually fired. The
 * component never infers an organ from a drug name: warfarin does not shade a
 * liver because warfarin is hepatically cleared, it shades one when THIS
 * patient's assessment raised a hepatic finding.
 *
 * `organs_unmapped` is rendered, not swallowed. Several findings — a narrow
 * therapeutic index, a prior reaction to the class — have no organ, and a
 * diagram that quietly omitted them would invite a reader to count organs and
 * believe they had seen the whole assessment.
 */

import { useState } from "react";

export type OrganImpact = {
  organ: string;
  severity: "block" | "high" | "moderate" | "low";
  weight: number;
  reasons: string[];
};

/** Fill and stroke per severity. Deliberately the same ramp as the verdict
 *  badges elsewhere, so an amber organ and an amber verdict mean one thing. */
const SEVERITY_STYLE: Record<string, { fill: string; stroke: string; label: string }> = {
  block:    { fill: "fill-red-600/70",    stroke: "stroke-red-700",    label: "Blocked" },
  high:     { fill: "fill-red-500/55",    stroke: "stroke-red-600",    label: "High" },
  moderate: { fill: "fill-amber-400/55",  stroke: "stroke-amber-500",  label: "Moderate" },
  low:      { fill: "fill-yellow-300/45", stroke: "stroke-yellow-400", label: "Low" },
};

const IDLE = { fill: "fill-slate-200/40 dark:fill-slate-700/40", stroke: "stroke-slate-300 dark:stroke-slate-600" };

/** Anatomy, roughly. Positions only need to be recognisable on a torso — this
 *  is a wayfinding diagram, not a plate from Gray's. */
const SHAPES: Record<string, { d: string; label: string; cx: number; cy: number }> = {
  brain:      { d: "M100 18 C82 18 70 31 70 46 C70 60 82 70 100 70 C118 70 130 60 130 46 C130 31 118 18 100 18 Z", label: "Brain", cx: 100, cy: 44 },
  lungs:      { d: "M74 96 C62 100 58 124 62 148 C64 162 76 166 82 158 C88 148 88 116 86 100 Z M126 96 C138 100 142 124 138 148 C136 162 124 166 118 158 C112 148 112 116 114 100 Z", label: "Lungs", cx: 100, cy: 128 },
  heart:      { d: "M100 112 C94 102 78 104 78 120 C78 134 94 146 100 152 C106 146 122 134 122 120 C122 104 106 102 100 112 Z", label: "Heart", cx: 100, cy: 128 },
  liver:      { d: "M70 172 C70 164 96 162 112 168 C122 172 124 188 116 194 C104 202 74 198 70 186 Z", label: "Liver", cx: 92, cy: 182 },
  stomach:    { d: "M118 176 C130 174 138 184 136 196 C134 208 122 212 116 204 C112 198 112 182 118 176 Z", label: "Stomach", cx: 126, cy: 192 },
  kidneys:    { d: "M74 212 C66 212 62 222 64 234 C66 244 76 246 80 238 C84 230 82 212 74 212 Z M126 212 C134 212 138 222 136 234 C134 244 124 246 120 238 C116 230 118 212 126 212 Z", label: "Kidneys", cx: 100, cy: 228 },
  intestines: { d: "M76 252 C76 246 124 246 124 252 C124 266 112 262 112 272 C112 282 88 282 88 272 C88 262 76 266 76 252 Z", label: "Intestines", cx: 100, cy: 264 },
  blood:      { d: "M100 200 L104 214 L100 300 L96 214 Z", label: "Blood", cx: 100, cy: 250 },
  skin:       { d: "M100 12 C60 12 46 60 46 140 C46 230 58 300 68 330 L132 330 C142 300 154 230 154 140 C154 60 140 12 100 12 Z", label: "Skin", cx: 100, cy: 320 },
};

/** Skin is the outline itself, so it draws first and unfilled unless implicated. */
const DRAW_ORDER = ["skin", "brain", "lungs", "heart", "liver", "stomach", "kidneys", "intestines", "blood"];

export function BodyImpact({
  organs,
  unmapped = [],
  title,
  compact = false,
}: {
  organs: OrganImpact[];
  unmapped?: string[];
  title?: string;
  compact?: boolean;
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  const byOrgan = new Map(organs.map((o) => [o.organ, o]));
  const active = hovered ? byOrgan.get(hovered) : null;

  return (
    <div className="flex flex-col gap-3">
      {title ? <h4 className="text-sm font-medium text-slate-700 dark:text-slate-200">{title}</h4> : null}

      <div className={compact ? "flex gap-4" : "flex flex-col gap-4 sm:flex-row"}>
        <svg
          viewBox="0 0 200 345"
          className={compact ? "h-56 w-auto shrink-0" : "h-72 w-auto shrink-0"}
          role="img"
          aria-label={
            organs.length
              ? `Body diagram. Affected: ${organs.map((o) => `${o.organ} (${o.severity})`).join(", ")}.`
              : "Body diagram. No organ-specific findings."
          }
        >
          {DRAW_ORDER.map((name) => {
            const shape = SHAPES[name];
            if (!shape) return null;
            const impact = byOrgan.get(name);
            const style = impact ? SEVERITY_STYLE[impact.severity] ?? IDLE : IDLE;
            const isSkin = name === "skin";
            return (
              <path
                key={name}
                d={shape.d}
                className={[
                  isSkin && !impact ? "fill-transparent" : style.fill,
                  style.stroke,
                  "transition-opacity",
                  impact ? "cursor-pointer" : "pointer-events-none",
                  hovered && hovered !== name ? "opacity-40" : "opacity-100",
                ].join(" ")}
                strokeWidth={isSkin ? 1.5 : 1}
                onMouseEnter={() => impact && setHovered(name)}
                onMouseLeave={() => setHovered(null)}
              />
            );
          })}
        </svg>

        <div className="min-w-0 flex-1 space-y-2">
          {organs.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400">
              No organ-specific findings for this patient.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {organs.map((o) => {
                const style = SEVERITY_STYLE[o.severity] ?? IDLE;
                return (
                  <li
                    key={o.organ}
                    className={[
                      "rounded-md border px-2.5 py-1.5 text-sm transition-colors",
                      hovered === o.organ
                        ? "border-slate-400 bg-slate-50 dark:border-slate-500 dark:bg-slate-800"
                        : "border-slate-200 dark:border-slate-700",
                    ].join(" ")}
                    onMouseEnter={() => setHovered(o.organ)}
                    onMouseLeave={() => setHovered(null)}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium capitalize text-slate-800 dark:text-slate-100">
                        {SHAPES[o.organ]?.label ?? o.organ}
                      </span>
                      <span className={`rounded px-1.5 py-0.5 text-xs ${style.fill} ${style.stroke} border`}>
                        {style.label}
                      </span>
                    </div>
                    {/* The findings themselves, so the shading is never the only
                        statement — a colour a reader cannot trace to a reason is
                        a colour they have to take on trust. */}
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
              Not shown on the diagram: {unmapped.join(", ").toLowerCase().replace(/_/g, " ")} —
              these findings are not specific to one organ.
            </p>
          )}
        </div>
      </div>

      {active && (
        <p className="sr-only" aria-live="polite">
          {active.organ}: {active.severity}. {active.reasons.join(". ")}
        </p>
      )}
    </div>
  );
}

/**
 * Two bodies, for a substitution.
 *
 * The question a physician asks of an analogue is not "is this drug safe" but
 * "does it move the problem". So the summary leads with what the swap does not
 * change: a view showing only what it relieves would make every substitution
 * look like an improvement, and most are a trade.
 */
export function BodyImpactComparison({
  current,
  candidate,
  currentName,
  candidateName,
  relieved = [],
  introduced = [],
  unchanged = [],
  unmapped = [],
}: {
  current: OrganImpact[];
  candidate: OrganImpact[];
  currentName: string;
  candidateName: string;
  relieved?: string[];
  introduced?: string[];
  unchanged?: string[];
  unmapped?: string[];
}) {
  return (
    <div className="space-y-4">
      <div className="grid gap-6 sm:grid-cols-2">
        <BodyImpact organs={current} title={`Current — ${currentName}`} compact />
        <BodyImpact organs={candidate} title={`Substitute — ${candidateName}`} compact />
      </div>

      <div className="rounded-lg border border-slate-200 p-3 text-sm dark:border-slate-700">
        <p className="mb-2 font-medium text-slate-700 dark:text-slate-200">What the swap changes</p>
        <dl className="space-y-1">
          {relieved.length > 0 && (
            <div className="flex gap-2">
              <dt className="w-24 shrink-0 text-emerald-700 dark:text-emerald-400">Relieves</dt>
              <dd className="capitalize text-slate-700 dark:text-slate-200">{relieved.join(", ")}</dd>
            </div>
          )}
          {introduced.length > 0 && (
            <div className="flex gap-2">
              <dt className="w-24 shrink-0 text-red-700 dark:text-red-400">Introduces</dt>
              <dd className="capitalize text-slate-700 dark:text-slate-200">{introduced.join(", ")}</dd>
            </div>
          )}
          {unchanged.length > 0 && (
            <div className="flex gap-2">
              <dt className="w-24 shrink-0 text-slate-500 dark:text-slate-400">Unchanged</dt>
              <dd className="capitalize text-slate-700 dark:text-slate-200">{unchanged.join(", ")}</dd>
            </div>
          )}
          {relieved.length === 0 && introduced.length === 0 && unchanged.length === 0 && (
            <p className="text-slate-500 dark:text-slate-400">
              Neither drug raises an organ-specific finding for this patient.
            </p>
          )}
        </dl>
        {unmapped.length > 0 && (
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            {unmapped.length} finding{unmapped.length > 1 ? "s are" : " is"} not organ-specific and
            {unmapped.length > 1 ? " are" : " is"} not reflected above.
          </p>
        )}
      </div>
    </div>
  );
}
