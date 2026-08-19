"use client";

import { useCallback, useEffect, useState } from "react";
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
      .catch(() => {
        // Cache successes, never failures. Holding on to a rejected fetch means
        // one blip while compliance restarts costs every green badge its "N
        // rules evaluated" line for the rest of the session -- and the dialog
        // would quietly fall back to the weaker wording with nothing on screen
        // to say why. Clearing it lets the next dialog try again.
        rulesetCache = null;
        // Null rather than a throw: the explanation degrades to the wording
        // that does not depend on the ruleset, and the dialog still opens.
        return null;
      });

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
export function useCertificationStatuses(ndcs: string[], reloadKey: number = 0) {
  const key = Array.from(new Set(ndcs.filter(Boolean))).sort().join(",");
  const [results, setResults] = useState<Record<string, CertResult>>({});

  useEffect(() => {
    if (!key) {
      setResults({});
      return;
    }
    // Paged, not truncated. This used to `.slice(0, MAX_BATCH)`, which silently
    // dropped every NDC past the first hundred -- and `key` is sorted, so it was
    // always the same alphabetical tail that vanished. Those rows then fell
    // through to `unavailable` and rendered grey forever, indistinguishable from
    // a drug the FDA holds no record for, however many certifications the
    // database actually had. A shelf of 111 showed 11 permanently unknown.
    //
    // The 100 is the server's own cap on /status ("One page of stock, max 100"),
    // so the fix is to send more than one page rather than to raise it.
    const wanted = key.split(",");
    const pages: string[][] = [];
    for (let i = 0; i < wanted.length; i += MAX_BATCH) {
      pages.push(wanted.slice(i, i + MAX_BATCH));
    }
    let cancelled = false;

    Promise.all(
      pages.map((page) =>
        apiFetch(
          "compliance",
          `/status?${page.map((n) => `ndc=${encodeURIComponent(n)}`).join("&")}`,
        // One dead page must not blank the others: a partial shelf with real
        // colours beats a whole shelf of grey.
        ).catch(() => null),
      ),
    )
      .then((bodies) => {
        if (cancelled) return;
        const next: Record<string, CertResult> = {};
        for (const body of bodies) {
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
    // reloadKey, not a returned reload(): three callers treat this hook's
    // return as a plain record, and a bulk re-check needs the badges to
    // refresh without changing that shape at every call site.
  }, [key, reloadKey]);

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
  // Bumped by reload(). Not folded into `ndc`, because a re-check keeps looking
  // at the same drug and the effect has to re-run anyway.
  const [nonce, setNonce] = useState(0);

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
  }, [ndc, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { detail, error, loading, reload };
}

/**
 * Re-run the gates against freshly fetched upstream data (COMP-2), for one drug.
 *
 * `POST /explore` re-fetches and upserts unconditionally — unlike opening the
 * dialog, which only explores on a miss or an expired row. That is the whole
 * point of the button: a pharmacist who has just read a recall notice should
 * not have to wait out a seven-day TTL to see it reflected here.
 *
 * It costs two upstream calls against a shared daily budget, which is why it is
 * a deliberate click rather than something the dialog does on open.
 */
export async function recheckCertification(ndc: string): Promise<void> {
  const body = await apiFetch("compliance", "/explore", {
    method: "POST",
    body: JSON.stringify({ ndc: [ndc] }),
  });
  // A per-NDC upstream failure is a 200 with an `errors` entry — the endpoint is
  // built for batches, where one dead lookup must not lose the answers that did
  // come back. For a batch of one that would otherwise read as success and the
  // dialog would redisplay the stale verdict as though it were fresh.
  const failure = (body?.errors ?? {})[ndc];
  if (failure) throw new Error(String(failure));
}

/**
 * `MAX_EXPLORE` in services/compliance/app/main.py. Duplicated rather than
 * fetched: the server rejects an over-long batch with a 400, so the only thing
 * a wrong value here changes is whether the user sees that 400 or a clean split.
 */
const EXPLORE_BATCH = 10;

/**
 * Re-check many drugs in one action — COMP-2 across a whole shelf.
 *
 * Exists because the per-drug button works: a delisted or never-fetched NDC
 * usually resolves the moment /explore asks upstream directly, and doing that
 * one dialog at a time is not a workflow anyone should be asked to perform on a
 * hundred drugs.
 *
 * Sequential, not parallel, and that is the point rather than an oversight.
 * Each NDC costs two upstream calls against a shared daily openFDA budget, so
 * this deliberately trickles: overlapping the batches would empty the budget
 * faster without finishing meaningfully sooner, and the endpoint is documented
 * as being for "a handful of NDCs at once".
 *
 * Resolves rather than throws on partial failure. A batch where nine drugs
 * resolved and one upstream lookup died is mostly a success, and throwing would
 * discard the nine — the same reasoning the endpoint itself uses for returning
 * `errors` alongside `results`.
 */
export async function recheckCertifications(
  ndcs: string[],
  onProgress?: (done: number, total: number) => void,
): Promise<{ ok: number; errors: Record<string, string> }> {
  const wanted = Array.from(new Set(ndcs.filter(Boolean)));
  const errors: Record<string, string> = {};
  let ok = 0;

  for (let i = 0; i < wanted.length; i += EXPLORE_BATCH) {
    const chunk = wanted.slice(i, i + EXPLORE_BATCH);
    try {
      const body = await apiFetch("compliance", "/explore", {
        method: "POST",
        body: JSON.stringify({ ndc: chunk }),
      });
      const chunkErrors = (body?.errors ?? {}) as Record<string, string>;
      Object.assign(errors, chunkErrors);
      ok += chunk.length - Object.keys(chunkErrors).length;
    } catch (e) {
      // A whole-batch failure (403, 400, network) is attributed to every NDC in
      // it, so the count at the end still adds up to what was asked for.
      const message = e instanceof Error ? e.message : "request failed";
      for (const n of chunk) errors[n] = message;
    }
    onProgress?.(Math.min(i + EXPLORE_BATCH, wanted.length), wanted.length);
  }
  return { ok, errors };
}
