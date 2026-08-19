// Client-side port of parse_strength_and_form() from
// shared/medstock_shared/rxnorm.py (the _STRENGTH regex). RxNorm SCD/SBD
// display names put pack volume first for liquids ("0.4 ML enoxaparin sodium
// 100 MG/ML Prefilled Syringe"), which reads amount-first in tables. This
// splits a name so the ingredient (and brand) leads and strength/form demote
// to secondary detail. Display-only — the raw RxNorm string stays the stored
// identity and is always available as `raw` / the title attribute.

// Differs from the Python regex in two display-driven ways: the slash part
// accepts a bare unit ("100 MG/ML" — RxNorm rarely puts a number after the
// slash), and a trailing "Dose" token is consumed with its strength
// ("0.5 MG Dose" in multi-dose pen names) so it can't orphan into the name.
const STRENGTH =
  /\d+(?:\.\d+)?\s*(?:MG|MCG|UG|G|ML|MEQ|UNT|UNIT|%)(?:\s*\/\s*\d*(?:\.\d+)?\s*(?:ML|HR|ACTUAT))?(?:\s+Dose\b)?/gi;
const BRAND = /\[([^\]]+)\]/;

export type DrugNameParts = {
  /** Ingredient-first display name, brand appended when present: "insulin glargine (Lantus)" */
  primary: string;
  /** Strength(s) and dose form: "0.4 ML, 100 MG/ML · Prefilled Syringe" — null when unparseable */
  detail: string | null;
  raw: string;
};

export function parseDrugName(raw: string): DrugNameParts {
  const brand = raw.match(BRAND)?.[1]?.trim() || null;
  const base = raw.replace(BRAND, " ");
  const matches = [...base.matchAll(STRENGTH)];

  const withBrand = (name: string) =>
    brand && !name.toLowerCase().includes(brand.toLowerCase()) ? `${name} (${brand})` : name;

  if (matches.length === 0) {
    const primary = base.replace(/\s+/g, " ").trim() || raw;
    return { primary: withBrand(primary), detail: null, raw };
  }

  const last = matches[matches.length - 1];
  const doseForm = base
    .slice((last.index ?? 0) + last[0].length)
    .replace(/^[\s\][]+|[\s\][]+$/g, "");

  let name = base;
  for (const m of matches) name = name.replace(m[0], " ");
  if (doseForm) name = name.replace(doseForm, " ");
  name = name
    .replace(/\s+/g, " ")
    .replace(/\s*,\s*(?=,)/g, "")
    .replace(/^[\s,]+|[\s,]+$/g, "")
    .trim();

  const strength = matches.map((m) => m[0].replace(/\s+/g, " ").trim()).join(", ");
  const detail = [strength, doseForm].filter(Boolean).join(" · ") || null;
  return { primary: withBrand(name) || raw, detail, raw };
}
