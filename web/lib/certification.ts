/**
 * Why a drug is that colour — in words, from the same data the colour came from.
 *
 * The traffic light is a verdict, and a verdict without its reasoning is
 * something a pharmacist is right to distrust (docs/compliance-usecases.md §3).
 * The dialog already listed *findings*, which answers "why is this red?" well
 * enough. It answered "why is this green?" with an absence — no findings, no
 * text — and an absence reads identically to "nobody looked".
 *
 * So this builds an explanation for all four states, and is careful about what
 * green actually licenses: **no rule fired**, which is not the same claim as
 * "this drug is safe". Every sentence here has to survive a pharmacist reading
 * it next to the FDA page it cites.
 *
 * Pure, and separate from the dialog, because the copilot drawer shows the same
 * verdict and must not invent a second wording for it.
 */

import type {
  CertDetail,
  CertFinding,
  CertStatus,
  Ruleset,
} from "@/components/CertificationBadge";

export type CertExplanation = {
  /** One sentence: the verdict and what drove it. */
  headline: string;
  /** What that verdict does and does not license. Never omitted. */
  caveat: string;
  /** The findings that actually set the colour — usually one, sometimes several. */
  decisive: CertFinding[];
  /** Findings that did not set the colour but are on the record. */
  other: CertFinding[];
  /** For green: what was checked. Empty when the ruleset has not loaded. */
  checked: { category: string; rules: number }[];
};

const SEVERITY_RANK: Record<string, number> = { red: 0, yellow: 1, info: 2 };

/** The colour a status is driven by, so "why red" can name the red findings. */
const DRIVING_SEVERITY: Partial<Record<CertStatus, "red" | "yellow">> = {
  red: "red",
  yellow: "yellow",
};

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * Group the ruleset by category so green can say what ran rather than just
 * asserting that nothing was wrong.
 */
export function checksByCategory(ruleset: Ruleset | null): { category: string; rules: number }[] {
  if (!ruleset) return [];
  const counts = new Map<string, number>();
  for (const rule of Object.values(ruleset.rules)) {
    counts.set(rule.category, (counts.get(rule.category) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([category, rules]) => ({ category, rules }))
    .sort((a, b) => a.category.localeCompare(b.category));
}

export function explainCertification(
  detail: CertDetail | null,
  ruleset: Ruleset | null,
  opts: { unreachable?: boolean } = {},
): CertExplanation {
  const checked = checksByCategory(ruleset);

  // Not a backend status. The service was unreachable, so nothing was checked —
  // and the one thing this must never do is let that read as clean.
  if (opts.unreachable || !detail) {
    return {
      headline: "Not checked — the compliance service could not be reached.",
      caveat:
        "This is not a clean bill of health. No FDA record was read, so no rule was evaluated. " +
        "Treat it as unknown, not as certified.",
      decisive: [],
      other: [],
      checked: [],
    };
  }

  const findings = detail.findings ?? [];
  const sorted = [...findings].sort(
    (a, b) => (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9),
  );

  if (detail.status === "unknown") {
    return {
      headline: "No FDA certification record is held for this NDC.",
      caveat: detail.explore_error
        ? `An on-demand lookup was attempted and failed: ${detail.explore_error}. ` +
          "Nothing was checked, so this is not a clean bill of health."
        : "Nothing was checked, so this is not a clean bill of health. Opening this " +
          "dialog asks the FDA directly; if the NDC is real, the colour will resolve.",
      decisive: [],
      other: sorted,
      checked: [],
    };
  }

  const driving = DRIVING_SEVERITY[detail.status];
  if (driving) {
    const decisive = sorted.filter((f) => f.severity === driving);
    const other = sorted.filter((f) => f.severity !== driving);
    const word = detail.status === "red" ? "Not certified" : "Attention";

    // Transient vs standing is the distinction that decides what anyone does
    // next: a recall closes and a shortage resolves, a dead listing does not.
    const allTransient = decisive.length > 0 && decisive.every((f) => f.transient);
    const allStanding = decisive.length > 0 && decisive.every((f) => !f.transient);

    const headline =
      decisive.length === 1
        ? `${word} — ${decisive[0].message || decisive[0].code}.`
        : `${word} — ${plural(decisive.length, "reason", "reasons")} at ${driving} severity.`;

    // Deliberately does not name an example kind of event. Every lifecycle rule
    // in the ruleset is classed transient -- a listing can be re-registered, a
    // wind-down date can pass -- so wording like "a recall closes" reads plainly
    // wrong on an obsolete NDC, which is the commonest red there is.
    const caveat = allTransient
      ? "Every reason here has an end: it describes something that happened, not a permanent " +
        "property of the product. The colour is expected to change when the situation does, " +
        "and it is a re-check that changes it — not time passing."
      : allStanding
        ? "These are standing properties of the product rather than events. They will read the " +
          "same next year unless the listing itself changes, so waiting will not clear this."
        : "Some reasons here will clear on their own and some will not — the standing ones " +
          "are the ones worth acting on.";

    return { headline, caveat, decisive, other, checked };
  }

  // Green. The interesting case, and the one that used to render as silence.
  const info = sorted.filter((f) => f.severity === "info");
  const ruleCount = ruleset ? Object.keys(ruleset.rules).length : 0;

  const headline = ruleCount
    ? `Certified — ${plural(ruleCount, "rule", "rules")} evaluated against the FDA record, ` +
      "none of them disqualifying."
    : "Certified — no disqualifying finding on the FDA record.";

  // The honest limit of a green light, stated every time. `evaluate()` reports
  // what it could not check as an INFO finding, which is why an empty info list
  // is meaningful rather than merely quiet.
  const caveat = info.length
    ? "Green means no rule fired at red or amber — not that everything was verifiable. " +
      `${plural(info.length, "check", "checks")} below could not be completed, and that gap ` +
      "is the reason to read them rather than skip them."
    : "Green means no rule fired: actively marketed, no open recall, not in shortage, and " +
      "sold under an approved marketing category. It is a statement about the FDA record " +
      "as of the check below — not an inspection of the physical stock on your shelf.";

  return { headline, caveat, decisive: [], other: info, checked };
}

// --- the gates -------------------------------------------------------------

/**
 * A colour is the output of five independent questions, and the badge collapses
 * all five into one word. That is the right default — a shelf of eleven drugs
 * cannot show forty findings — but it means a pharmacist reading "Attention"
 * cannot tell a supply problem from a regulatory one without opening the list
 * and inferring it from the codes.
 *
 * So the dialog shows the gates themselves: what each one asks, how many rules
 * it holds, and whether this drug got through. The questions below are the
 * category comments from `medstock_shared.certification.Category`, kept in the
 * same words on purpose — two vocabularies for one taxonomy is how a UI starts
 * quietly disagreeing with the service behind it.
 */
export type GateVerdict = "pass" | "yellow" | "red" | "not-run";

export type Gate = {
  category: string;
  question: string;
  rules: number;
  verdict: GateVerdict;
  fired: CertFinding[];
};

/**
 * Pipeline order, not alphabetical: is this a real product, may it be sold,
 * has anyone acted against it, can we get it, and what could we not check.
 * A reader scanning left to right should be walking the argument.
 */
const GATE_ORDER = ["lifecycle", "approval", "enforcement", "supply", "data"] as const;

const GATE_QUESTION: Record<string, string> = {
  lifecycle: "Is it still a marketed product?",
  approval: "What authority is it sold under?",
  enforcement: "Recalls and regulatory action",
  supply: "Can it actually be obtained?",
  data: "What could we not check?",
};

export function gatesFor(detail: CertDetail | null, ruleset: Ruleset | null): Gate[] {
  if (!ruleset) return [];

  const counts = new Map<string, number>();
  for (const rule of Object.values(ruleset.rules)) {
    counts.set(rule.category, (counts.get(rule.category) ?? 0) + 1);
  }

  // Unknown and unavailable are not passes. Nothing ran, and a row of green
  // gates over a grey badge would be the single most misleading thing on the
  // page — it would say "five checks cleared" about a drug nobody looked at.
  const ran = Boolean(detail) && detail!.status !== "unknown";

  const known = new Set<string>(GATE_ORDER);
  const categories = [
    ...GATE_ORDER.filter((c) => counts.has(c)),
    // Anything the service grew that this file has not been taught about yet.
    // Better to render it unordered and unlabelled than to drop a gate silently.
    ...[...counts.keys()].filter((c) => !known.has(c)).sort(),
  ];

  return categories.map((category) => {
    const fired = (detail?.findings ?? []).filter((f) => f.category === category);
    const worst = fired.some((f) => f.severity === "red")
      ? "red"
      : fired.some((f) => f.severity === "yellow")
        ? "yellow"
        : null;
    return {
      category,
      question: GATE_QUESTION[category] ?? category,
      rules: counts.get(category) ?? 0,
      // An info-only finding still passes the gate: it is a note about what
      // could not be checked, not a reason to hold the drug.
      verdict: !ran ? "not-run" : (worst ?? "pass"),
      fired,
    };
  });
}

/**
 * Who may spend a re-check.
 *
 * `certification:explore` is narrower than `certificate:read` because each call
 * is two upstream requests against a shared daily openFDA budget. Same shape as
 * `approvalStance` in lib/prognosis.ts, and for the same reason: when the role
 * cannot be confirmed the control is offered and the server decides, because
 * gating on auth being reachable would put auth back in the critical path of a
 * page that deliberately does not depend on it.
 */
export type ExploreStance = "allowed" | "denied" | "unconfirmed";

export function exploreStance(role: string | undefined): ExploreStance {
  if (role === undefined) return "unconfirmed";
  return role === "pharmacist" || role === "admin" ? "allowed" : "denied";
}
