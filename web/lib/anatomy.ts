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
 * Coordinates are in the file's own viewBox units, so the overlay can share its
 * coordinate space directly.
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

/**
 * One figure for every patient.
 *
 * There were two, picked by recorded sex, and that was the wrong design for
 * what this drawing does. The marks are viscera — liver, kidneys, stomach —
 * and none of them sits differently for a man than a woman at this scale. The
 * only thing the sex switch changed was the silhouette around them, so it
 * bought no anatomical accuracy while costing plenty: a patient whose sex was
 * unrecorded got a caption apologising for a body it had guessed, a patient
 * whose sex did not fit two letters got the same, and each template needed its
 * own anchor set, so any change to the mapping had to be made and verified
 * twice.
 *
 * Kept the female-derived artwork because it is the better drawing of the two
 * — cleaner organ separation and a more legible abdomen — and it is used here
 * as a neutral anatomical frame, not as a depiction of the patient. The file
 * keeps its upstream name so it stays traceable to PROVENANCE.md; the UI never
 * shows that name or calls the figure gendered.
 */
export const FIGURE: AnatomyTemplate = {
  src: "/anatomy/female_template_with_organs.svg",
  viewBox: [1453.8667, 2320],
  label: "anatomical figure",
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
};

/** The figure to draw. Takes no argument: there is one, for everybody. */
export function anatomyFigure(): AnatomyTemplate {
  return FIGURE;
}

/** Provenance, surfaced in the UI. A hospital product should be able to answer
 *  where its assets came from without anyone digging through a repo. */
export const ANATOMY_CREDIT = {
  author: "Mikael Häggström",
  source: "Wikimedia Commons",
  license: "CC0 1.0 Universal (public domain dedication)",
  url: "https://commons.wikimedia.org/wiki/Human_body_diagrams",
} as const;
