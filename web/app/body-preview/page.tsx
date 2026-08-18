/**
 * A standalone render of AnatomyImpact, so the figure can be looked at without
 * standing up auth, patient-profiling and a seeded cart first.
 *
 * The data is real output from `medstock_shared.organs.impacts()`. The second
 * case exercises the organs that only the widened reaction table can reach —
 * pancreatitis, cholestasis, oesophageal injury — because an organ that is
 * drawn but unreachable is decoration, and this page is where that would show.
 */

import { AnatomyImpact, type OrganImpact } from "@/components/AnatomyImpact";

const IBUPROFEN: OrganImpact[] = [
  {
    organ: "kidneys",
    severity: "high",
    weight: 55,
    reasons: [
      "eGFR 30-44: dose exceeds renal guidance",
      "Already taking an NSAID",
    ],
  },
  { organ: "stomach", severity: "moderate", weight: 35, reasons: ["Already taking an NSAID"] },
  {
    organ: "oesophagus",
    severity: "moderate",
    weight: 35,
    reasons: ["Already taking an NSAID"],
  },
  {
    organ: "liver",
    severity: "moderate",
    weight: 20,
    reasons: ["Hepatic impairment: reduced clearance"],
  },
  {
    organ: "blood",
    severity: "low",
    weight: 10,
    reasons: ["Gastrointestinal haemorrhage reported disproportionately"],
  },
];

const SERTRALINE: OrganImpact[] = [
  {
    organ: "liver",
    severity: "moderate",
    weight: 20,
    reasons: ["Hepatic impairment: reduced clearance"],
  },
  { organ: "brain", severity: "low", weight: 5, reasons: ["Dizziness reported disproportionately"] },
];

/** Everything the widened table can now reach, in one figure. */
const WIDE: OrganImpact[] = [
  { organ: "pancreas", severity: "high", weight: 40, reasons: ["Acute pancreatitis reported disproportionately"] },
  { organ: "gallbladder", severity: "moderate", weight: 20, reasons: ["Cholestatic jaundice reported"] },
  { organ: "thyroid", severity: "moderate", weight: 15, reasons: ["Hypothyroidism reported"] },
  { organ: "bladder", severity: "low", weight: 10, reasons: ["Urinary retention reported"] },
  { organ: "spleen", severity: "low", weight: 5, reasons: ["Splenomegaly reported"] },
];

export default function BodyPreviewPage() {
  return (
    <main className="mx-auto max-w-5xl space-y-8 p-8">
      <header>
        <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
          Doreen Whitfield, b.1946 — ibuprofen
        </h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          eGFR 31 · hepatic impaired · already on an NSAID. Shading comes from the
          findings <code>assess()</code> produced, not from the drug.
        </p>
      </header>

      <section className="rounded-xl border border-slate-200 p-6 dark:border-slate-700">
        <AnatomyImpact
          organs={IBUPROFEN}
          unmapped={["NARROW_THERAPEUTIC_INDEX"]}
          sex="F"
          title="Single candidate"
        />
      </section>

      <section className="rounded-xl border border-slate-200 p-6 dark:border-slate-700">
        <h2 className="mb-4 text-sm font-medium text-slate-700 dark:text-slate-200">
          Substitution
        </h2>
        <div className="grid gap-8 sm:grid-cols-2">
          <AnatomyImpact organs={IBUPROFEN} sex="F" title="Current — ibuprofen" height={300} />
          <AnatomyImpact organs={SERTRALINE} sex="F" title="Substitute — sertraline" height={300} />
        </div>
        <p className="mt-4 border-t border-slate-200 pt-3 text-sm text-slate-600 dark:border-slate-700 dark:text-slate-400">
          Relieves kidneys, stomach, oesophagus and the bleeding risk. Introduces a
          nervous-system finding. Liver unchanged.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 p-6 dark:border-slate-700">
        <AnatomyImpact
          organs={WIDE}
          sex="M" title="Organs only the widened reaction table can reach"
        />
      </section>
    </main>
  );
}
