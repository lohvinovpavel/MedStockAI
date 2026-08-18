"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";

/**
 * PP-5 approval gate — the client half.
 *
 * A risk profile is a model's reading of an FDA label. It is extracted offline,
 * lands `awaiting_approval`, and colours nothing anywhere in the product until a
 * pharmacist rules on it (docs/prognosis-and-procurement.md §1.3, gate 3).
 * This is the queue behind that ruling.
 */

export type ProfileStatus = "awaiting_approval" | "approved" | "rejected";

/** One condition from the label, expressed over the de-identified feature vector. */
export type RiskFactor = { feature: string; op: string; value: string | string[] };

export type RiskProfile = {
  id: number;
  rxcui: string;
  reaction: string;
  seriousness: string;
  risk_factors: RiskFactor[];
  /** Verbatim from the named label section. The reviewable basis — see below. */
  citation: string;
  section: string;
  spl_id: string | null;
  status: ProfileStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string;
  extracted_at: string | null;
};

export type QueueCounts = Record<ProfileStatus, number>;

export type Queue = {
  status: string;
  limit: number;
  items: RiskProfile[];
  counts: QueueCounts;
  /** null, never 0, until somebody has ruled on something. See §5.4. */
  accept_rate: number | null;
};

/**
 * Why the queue is not on screen, when it is not on screen.
 *
 * These must stay distinct all the way to the render. An empty list is a claim
 * — "there is nothing here to review" — and showing it because the service was
 * unreachable or the role was wrong tells a pharmacist their backlog is clear
 * when it is not. Same reasoning as COMP-1's `unavailable`
 * (components/CertificationBadge.tsx): a thing we could not check must never
 * borrow the appearance of one that came back clean.
 */
export type QueueState = "loading" | "ok" | "unauthenticated" | "forbidden" | "unavailable";

export const EMPTY_COUNTS: QueueCounts = { awaiting_approval: 0, approved: 0, rejected: 0 };

function stateFor(error: unknown): QueueState {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  return "unavailable";
}

export function useRiskProfileQueue(status: string) {
  const [queue, setQueue] = useState<Queue | null>(null);
  const [state, setState] = useState<QueueState>("loading");
  // Separate from `state` so a re-read never throws the rows away and puts a
  // skeleton in their place. The old numbers stay up, dimmed, and are replaced
  // when the new ones land — no layout jump between a ruling and its effect.
  const [refreshing, setRefreshing] = useState(false);
  // Bumped by a ruling, to re-read the queue and the counts it moved.
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setRefreshing(true);
    apiFetch("patients", `/risk-profiles?status=${encodeURIComponent(status)}`)
      .then((body) => {
        if (cancelled) return;
        setQueue(body as Queue);
        setState("ok");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        // Hold the previous rows rather than blanking the table: a failed
        // refresh after a ruling should not look like the queue emptying.
        setState(stateFor(error));
      })
      .finally(() => {
        if (!cancelled) setRefreshing(false);
      });
    return () => {
      cancelled = true;
    };
  }, [status, tick]);

  return { queue, state, refreshing, refresh };
}

export async function reviewProfile(id: number, action: "approve" | "reject", note: string) {
  return apiFetch("patients", `/risk-profiles/${id}/review`, {
    method: "POST",
    body: JSON.stringify({ action, note }),
  });
}

/**
 * Whether to offer the ruling controls. Three answers, not two.
 *
 * Only a pharmacist may rule (medstock_shared/auth.py), so a director — who can
 * read this page — is not offered a control that would always 403.
 *
 * `unconfirmed` is the case an end-to-end run turned up. `useSession` reports
 * `null` both for "logged out" and for "the auth service did not answer", and
 * with auth down a pharmacist holding a perfectly valid cookie was told their
 * *role* was insufficient — a false statement, and the buttons vanished for
 * someone whose ruling patient-profiling would have accepted. Every service
 * verifies the JWT locally precisely so auth is not a single point of failure
 * (auth.py's module docstring); gating the controls on auth being reachable put
 * that single point of failure back.
 *
 * So when the role cannot be confirmed the controls are offered and the server
 * decides. That is safe here: this only renders inside a loaded queue, and the
 * queue loading already proves a valid cookie carrying `profile:review`. If the
 * holder turns out not to be a pharmacist the ruling comes back 403 and the
 * toast says so — which is the server being the authority, as it should be.
 */
export type ApprovalStance = "allowed" | "denied" | "unconfirmed";

export function approvalStance(role: string | undefined): ApprovalStance {
  if (role === undefined) return "unconfirmed";
  return role === "pharmacist" ? "allowed" : "denied";
}

/** One row of the decision trail — `GET /assessments`. */
export type AssessmentRow = {
  request_id: string;
  actor_id: string;
  created_at: string | null;
  ruleset_version: string;
  drugs: string[];
  verdict: string | null;
};

/** One finding's share of a score — `GET /explain/{request_id}`. */
export type Contribution = {
  code: string;
  weight: number | null;
  stage: number | null;
  source: string | null;
  share: number | null;
};

export type Explanation = {
  request_id: string;
  assessed_by: string;
  assessed_at: string | null;
  ruleset_version: string;
  current_ruleset_version: string;
  explained_with_original_ruleset: boolean;
  caveat: string | null;
  assessments: {
    rxcui: string;
    verdict: string;
    score: number | null;
    blocked: boolean;
    band: { from_score: number; verdict: string; next_verdict: string | null; points_to_next: number | null } | null;
    contributions: Contribution[];
  }[];
};

/**
 * The decision trail behind the audit page.
 *
 * Shares `QueueState` with the review queue so the four ways a list can be
 * absent — loading, not signed in, wrong role, service down — stay one
 * vocabulary rather than two that drift.
 */
export function useAssessments() {
  const [rows, setRows] = useState<AssessmentRow[] | null>(null);
  const [state, setState] = useState<QueueState>("loading");

  useEffect(() => {
    let cancelled = false;
    apiFetch("patients", "/assessments?limit=25")
      .then((body) => {
        if (cancelled) return;
        setRows((body?.items ?? []) as AssessmentRow[]);
        setState("ok");
      })
      .catch((error: unknown) => {
        if (!cancelled) setState(stateFor(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { rows, state };
}

/** Fetched only when a row is opened — nobody reads every explanation. */
export function useExplanation(requestId: string | null) {
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!requestId) {
      setExplanation(null);
      setError(null);
      return;
    }
    let cancelled = false;
    apiFetch("patients", `/explain/${encodeURIComponent(requestId)}`)
      .then((body) => {
        if (!cancelled) setExplanation(body as Explanation);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [requestId]);

  return { explanation, error };
}
