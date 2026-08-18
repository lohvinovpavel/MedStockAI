/**
 * Where each organ sits on the anatomical templates.
 *
 * The artwork is Mikael Häggström's CC0 body templates from Wikimedia Commons.
 * They ship with callout labels ("Liver") and leader lines running to the organ,
 * so the coordinates below were **derived from the asset**, not hand-traced:
 * pair each label with its nearest leader line, take the far end. That means
 * they cannot drift from the artwork, and replacing the artwork is a matter of
 * re-running the derivation rather than re-measuring by eye.
 *
 * Three exceptions, all recorded rather than quietly fudged. `heart` and `lungs`
 * mispair — the cardiovascular leader points at a vessel in the shoulder rather
 * than the heart, and the lung leader lands near the diaphragm. Both are
 * interpolated instead: the midline comes from the three anchors that genuinely
 * sit on it (brain, oesophagus, bladder) and the chest depth from the neck-to-
 * liver span. `thyroid` has no callout on the template at all, so it is placed
 * at the base of the neck between the brain and oesophagus anchors. The UI
 * reported it as unplaced before this was added, which is the guard working:
 * an organ the mapping can shade but the figure cannot show says so.
 *
 * Verified by drawing markers over the render and looking.
 *
 * Coordinates are in each file's own viewBox units. The two templates differ in
 * size, which is why this is per-sex rather than one shared map.
 */

export type Point = readonly [number, number];

export type AnatomyTemplate = {
  /** Served from public/. */
  readonly src: string;
  /** The file's own viewBox, so an overlay can share its coordinate space. */
  readonly viewBox: readonly [number, number];
  readonly label: string;
  /** Organ key (see medstock_shared/organs.py) -> centre point. */
  readonly anchors: Readonly<Record<string, Point>>;
};

/** Radius of the highlight drawn at an anchor, in viewBox units.
 *
 *  Per organ, because a liver is not a gallbladder and one radius for both
 *  either swamps the small organs or under-marks the large ones. These are
 *  approximate on purpose: the highlight says LOOK HERE, it does not claim to
 *  trace an outline, and a circle that pretends to be an organ boundary would
 *  be making an anatomical claim the asset does not support. */
export const ORGAN_RADIUS: Readonly<Record<string, number>> = {
  brain: 78,
  oesophagus: 34,
  lungs: 96,
  heart: 58,
  liver: 82,
  gallbladder: 30,
  stomach: 58,
  spleen: 38,
  pancreas: 52,
  kidneys: 74,
  intestines: 100,
  bladder: 40,
  blood: 62,
  thyroid: 28,
  skin: 0, // whole-body; drawn as a wash rather than a spot
};

export const TEMPLATES: Readonly<Record<"M" | "F", AnatomyTemplate>> = {
  M: {
    src: "/anatomy/male_template_with_organs.svg",
    viewBox: [1363, 2440],
    label: "male",
    anchors: {
      brain: [671, 225],
      oesophagus: [644, 375],
      thyroid: [657, 337],
      lungs: [655, 550],
      heart: [693, 604],
      spleen: [903, 758],
      stomach: [698, 861],
      liver: [609, 862],
      pancreas: [669, 896],
      gallbladder: [582, 919],
      blood: [936, 950],
      kidneys: [566, 971],
      intestines: [556, 1091],
      bladder: [650, 1216],
    },
  },
  F: {
    src: "/anatomy/female_template_with_organs.svg",
    viewBox: [1453.8667, 2320],
    label: "female",
    anchors: {
      brain: [716, 186],
      oesophagus: [687, 345],
      thyroid: [701, 313],
      lungs: [699, 532],
      heart: [737, 589],
      spleen: [963, 754],
      stomach: [745, 864],
      liver: [649, 865],
      pancreas: [714, 901],
      gallbladder: [621, 926],
      blood: [998, 959],
      kidneys: [604, 982],
      intestines: [593, 1109],
      bladder: [693, 1243],
    },
  },
};

/** Which template to draw. An unrecorded sex falls back to the male frame and
 *  says so in the caption rather than silently asserting one — the figure is
 *  anatomical context, and pretending to know is worse than admitting we do
 *  not. */
export function templateFor(sex?: string | null): {
  template: AnatomyTemplate;
  known: boolean;
} {
  const key = (sex ?? "").trim().toUpperCase().slice(0, 1);
  if (key === "F") return { template: TEMPLATES.F, known: true };
  if (key === "M") return { template: TEMPLATES.M, known: true };
  return { template: TEMPLATES.M, known: false };
}

/** Provenance, surfaced in the UI. A hospital product should be able to answer
 *  where its assets came from without anyone digging through a repo. */
export const ANATOMY_CREDIT = {
  author: "Mikael Häggström",
  source: "Wikimedia Commons",
  license: "CC0 1.0 Universal (public domain dedication)",
  url: "https://commons.wikimedia.org/wiki/Human_body_diagrams",
} as const;
