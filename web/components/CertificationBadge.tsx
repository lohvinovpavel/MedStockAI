"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { cn } from "@/lib/utils";

/**
 * COMP-1 traffic light.
 *
 * `unavailable` is not a backend status — it is what the browser shows when
 * `compliance` cannot be reached. That distinction is the whole point of
 * fetching this separately from stock (docs/compliance-usecases.md §2.2): the
 * shelf renders from inventory, the badges from compliance, and one being down
 * never blanks the other.
 *
 * There is deliberately **no fallback to the row's stored certStatus**. A drug
 * whose certification we could not check must never render as certified — a
 * stale local "valid" is exactly the reassurance this feature exists to stop
 * anyone giving.
 */
export type CertStatus = "green" | "yellow" | "red" | "unknown" | "unavailable";

/** One row of `GET /status`. Shape mirrors medstock_shared.certification.signal(). */
export type CertResult = {
  status: CertStatus;
  /** Worst severity among *standing* reasons — a drug can be red on a recall
   *  that will clear, which is a different conversation from a dead listing. */
  attention?: CertStatus;
  reasons: number;
  transient?: number;
  persistent?: number;
  categories?: Record<string, number>;
  codes?: string[];
};

/**
 * Exported because the copilot drawer renders the same statuses in its
 * certificate card. One vocabulary, one place: a second copy would let the
 * drawer call an NDC "Certified" on the same screen where the shelf calls it
 * "Unknown", and nothing would flag the contradiction.
 */
export const CERT_LABELS: Record<CertStatus, string> = {
  green: "Certified",
  yellow: "Attention",
  red: "Not certified",
  unknown: "Unknown",
  unavailable: "Unavailable",
};

const LABELS = CERT_LABELS;

const TITLES: Record<CertStatus, string> = {
  green: "Actively marketed, no open recall",
  yellow: "Expiring soon, open recall, or an unapproved marketing category",
  red: "Listing or marketing expired, or a Class I recall is ongoing",
  unknown: "No FDA certification record held for this NDC",
  unavailable: "Compliance service unreachable — status not checked",
};

export const CERT_TONE: Record<CertStatus, StatusTone> = {
  green: "normal",
  yellow: "warning",
  red: "critical",
  unknown: "neutral",
  unavailable: "neutral",
};

const TONE = CERT_TONE;

// compliance caps a batch at 100; one page of stock is well inside that.
const MAX_BATCH = 100;

/**
 * The traffic light itself.
 *
 * Pass `onClick` and it becomes a button: the colour is a verdict, and a
 * verdict you cannot interrogate is one a pharmacist is right to distrust. The
 * evidence used to live behind a kebab menu, which is a strange place to hide
 * the answer to the question the badge itself provokes.
 *
 * `label` names the drug in the accessible name, so a screen reader hears
 * "Certification for Amoxicillin: Certified, 0 reasons. Show why." rather than
 * a row of identical buttons. Colour alone never carries the meaning — the text
 * is always there next to the dot, for a red/green reader as much as anyone.
 */
export function CertificationBadge({
  result,
  onClick,
  label,
}: {
  result?: CertResult;
  onClick?: () => void;
  /** Drug name, for the accessible name only. */
  label?: string;
}) {
  const status = result?.status ?? "unavailable";
  const reasons = result?.reasons ?? 0;

  const badge = (
    <StatusBadge
      tone={TONE[status]}
      className={cn("normal-case", onClick && "transition-colors group-hover:brightness-95")}
    >
      {LABELS[status]}
      {reasons > 0 ? ` · ${reasons}` : ""}
    </StatusBadge>
  );

  if (!onClick) return <span title={TITLES[status]}>{badge}</span>;

  return (
    <button
      type="button"
      onClick={onClick}
      title={`${TITLES[status]} — click for the reasoning`}
      aria-label={`Certification${label ? ` for ${label}` : ""}: ${LABELS[status]}, ${
        reasons === 1 ? "1 reason" : `${reasons} reasons`
      }. Show why.`}
      className="group cursor-pointer rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
    >
      {badge}
    </button>
  );
}

/**
 * `GET /ruleset` — every rule that can produce a colour, and the thresholds
 * behind them.
 *
 * Fetched so a green badge can say *what was checked* rather than asserting
 * that nothing was wrong. Cached at module scope: the ruleset changes when the
 * service is redeployed, not while someone is reading a stock table, and every
 * row on the page would otherwise ask for the same document.
 */
/** `GET /ruleset`. Mirrors medstock_shared.certification.ruleset(). */
export type Ruleset = {
  version: string;
  marketing_end_window_days: number;
  rules: Record<
    string,
    { severity: "red" | "yellow" | "info"; category: string; transient: boolean; explains: string }
  >;
  sources: Record<string, string>;
  notes: string[];
};

let rulesetCache: Promise<Ruleset | null> | null = null;

export function useRuleset() {
  const [ruleset, setRuleset] = useState<Ruleset | null>(null);

  useEffect(() => {
    let cancelled = false;
    rulesetCache ??= apiFetch("compliance", "/ruleset")
      .then((body) => body as Ruleset)
      // Null, not a throw: the explanation degrades to the wording that does
      // not depend on the ruleset, and the dialog still opens.
      .catch(() => null);
    rulesetCache.then((body) => {
      if (!cancelled) setRuleset(body);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return ruleset;
}

/**
 * One batched call for a whole page of stock, not one call per row.
 *
 * A failure resolves to an empty map rather than throwing: every row falls back
 * to `unavailable`, renders grey, and the stock table stays on screen.
 */
export function useCertificationStatuses(ndcs: string[]) {
  const key = Array.from(new Set(ndcs.filter(Boolean))).sort().join(",");
  const [results, setResults] = useState<Record<string, CertResult>>({});

  useEffect(() => {
    if (!key) {
      setResults({});
      return;
    }
    const wanted = key.split(",").slice(0, MAX_BATCH);
    const query = wanted.map((n) => `ndc=${encodeURIComponent(n)}`).join("&");
    let cancelled = false;

    apiFetch("compliance", `/status?${query}`)
      .then((body) => {
        if (cancelled) return;
        const next: Record<string, CertResult> = {};
        for (const row of (body?.results ?? []) as (CertResult & { ndc: string })[]) {
          next[row.ndc] = {
            status: row.status,
            attention: row.attention,
            reasons: row.reasons,
            transient: row.transient,
            persistent: row.persistent,
            categories: row.categories,
            codes: row.codes,
          };
        }
        setResults(next);
      })
      .catch(() => {
        // Degrade to grey. See the note on CertStatus above.
        if (!cancelled) setResults({});
      });

    return () => {
      cancelled = true;
    };
  }, [key]);

  return results;
}

/** One finding as returned by `GET /certificates/{ndc}`. */
export type CertFinding = {
  code: string;
  severity: "red" | "yellow" | "info";
  category: string;
  transient: boolean;
  message: string;
  source: string;
  source_url: string;
  source_ref: string;
  observed_at: string | null;
};

export type CertDetail = {
  ndc: string;
  status: CertStatus;
  ruleset_version?: string;
  provenance?: string;
  explored?: boolean;
  explore_error?: string;
  computed_at?: string | null;
  marketing_end_date?: string | null;
  listing_expiration_date?: string | null;
  marketing_category?: string | null;
  labeler?: string | null;
  findings: CertFinding[];
};

/**
 * The evidence behind one colour — what a pharmacist opens when they disagree.
 *
 * Fetched only when a certificate dialog is actually opened, because on a miss
 * this endpoint triggers COMP-2 exploration upstream, and that spends real
 * request budget. Passing a null ndc (dialog closed, or a received batch with
 * no NDC at all) does nothing.
 */
export function useCertificateDetail(ndc: string | null) {
  const [detail, setDetail] = useState<CertDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!ndc) {
      setDetail(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    apiFetch("compliance", `/certificates/${encodeURIComponent(ndc)}`)
      .then((body) => {
        if (!cancelled) setDetail(body as CertDetail);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [ndc]);

  return { detail, error, loading };
}
