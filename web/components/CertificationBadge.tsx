"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

/**
 * COMP-1 traffic light. `unavailable` is not a backend status — it is what the
 * browser shows when `compliance` cannot be reached.
 *
 * That distinction is the point of fetching this separately from stock
 * (docs/compliance-usecases.md §2.2): the shelf renders from `inventory`, the
 * badges from `compliance`, and one being down never blanks the other.
 */
export type CertStatus = "green" | "yellow" | "red" | "unknown" | "unavailable";

export type CertResult = { status: CertStatus; reasons: number };

const LABELS: Record<CertStatus, string> = {
  green: "Certified",
  yellow: "Attention",
  red: "Not certified",
  unknown: "Unknown",
  unavailable: "Unavailable",
};

const TITLES: Record<CertStatus, string> = {
  green: "Actively marketed, no open recall",
  yellow: "Expiring soon, open recall, or an unapproved marketing category",
  red: "Listing or marketing expired, or a Class I recall is ongoing",
  unknown: "No FDA certification record held for this NDC",
  unavailable: "Compliance service unreachable — status not checked",
};

// compliance caps a batch at 100; one page of stock is well inside that.
const MAX_BATCH = 100;

export function CertificationBadge({ result }: { result?: CertResult }) {
  const status = result?.status ?? "unavailable";
  const reasons = result?.reasons ?? 0;
  return (
    <span className={`cert-badge cert-badge-${status}`} title={TITLES[status]}>
      {LABELS[status]}
      {reasons > 0 ? ` · ${reasons}` : ""}
    </span>
  );
}

/**
 * One batched call for a whole page of stock, not one call per row.
 *
 * A failure resolves to an empty map rather than throwing: the caller renders
 * grey badges and the stock table stays on screen.
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
          next[row.ndc] = { status: row.status, reasons: row.reasons };
        }
        setResults(next);
      })
      .catch(() => {
        // Degrade to grey. A drug whose certification we could not check must
        // never render as certified.
        if (!cancelled) setResults({});
      });

    return () => {
      cancelled = true;
    };
  }, [key]);

  return results;
}
