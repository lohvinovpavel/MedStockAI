"use client";

import { useState } from "react";
import { FileSearch } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Callout } from "@/components/dashboard/Callout";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { useAssessments, useExplanation, type AssessmentRow } from "@/lib/prognosis";

/**
 * The real decision trail, and why each decision came out that way.
 *
 * docs/services.md §1.3 describes a complete audit trail and
 * patient-profiling-usecases.md §7 names the table. Both existed before this
 * component and neither was reachable: `assessment_log` recorded every
 * assessment, `/explain/{request_id}` could explain any of them, and nothing
 * listed the ids — so the trail was real in the database and invisible
 * everywhere else. This is the join.
 *
 * It carries **no patient identifier**, because the table does not. A reader
 * gets who asked, when, what came back, and the arithmetic behind it. "Which
 * patient was this about" is a question this cannot answer and the hospital's
 * own EHR can, which is exactly the split §2.4 describes.
 */

const VERDICT_TONE: Record<string, StatusTone> = {
  blocked: "stockout",
  red: "critical",
  amber: "warning",
  green: "normal",
};

function when(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The contribution breakdown for one logged decision. */
function Explanation({ requestId }: { requestId: string }) {
  const { explanation, error } = useExplanation(requestId);

  if (error) {
    return <p className="px-4 py-3 text-xs text-muted-foreground">Could not explain: {error}</p>;
  }
  if (!explanation) {
    return <p className="px-4 py-3 text-xs text-muted-foreground">Reading the basis&hellip;</p>;
  }

  return (
    <div className="flex flex-col gap-3 bg-muted/40 px-4 py-3">
      {/* The honesty flag from /explain: a decision made under an older ruleset
          explained with today&apos;s weights would look like a perfectly good
          answer and be a lie. */}
      {explanation.caveat && <Callout tone="warning">{explanation.caveat}</Callout>}

      {explanation.assessments.map((a) => (
        <div key={a.rxcui} className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs tabular-nums">{a.rxcui}</span>
            <StatusBadge tone={VERDICT_TONE[a.verdict] ?? "neutral"} className="normal-case">
              {a.verdict}
            </StatusBadge>
            {a.blocked ? (
              // A hard gate ends the pipeline and produces no score, because a
              // number beside an absolute contraindication invites someone to
              // weigh it against a discount.
              <span className="text-[11px] text-muted-foreground">
                hard gate — no score produced
              </span>
            ) : (
              <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                score {a.score}
                {a.band?.points_to_next != null
                  ? ` · ${a.band.points_to_next} to ${a.band.next_verdict}`
                  : ""}
              </span>
            )}
          </div>

          {a.contributions.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              Nothing flagged — every stage passed.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {a.contributions.map((c, i) => (
                <li key={`${c.code}-${i}`} className="flex items-center gap-2 text-[11px]">
                  {/* Share of the score as a bar. A weight-0 finding records
                      what was checked and contributed nothing, so it gets a
                      track and no fill rather than being hidden. */}
                  <span className="h-1.5 w-16 shrink-0 overflow-hidden rounded-[2px] bg-muted">
                    <span
                      className="block h-full rounded-[2px] bg-foreground/40"
                      style={{ width: `${Math.round((c.share ?? 0) * 100)}%` }}
                    />
                  </span>
                  <span className="font-mono">{c.code}</span>
                  <span className="text-muted-foreground">
                    {c.weight === 0 ? "informational" : `+${c.weight}`}
                  </span>
                  {c.source && (
                    <span className="truncate text-muted-foreground/70">{c.source}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}

      <p className="text-[11px] text-muted-foreground">
        Ruleset {explanation.ruleset_version} · request {explanation.request_id}
      </p>
    </div>
  );
}

export function DecisionTrail() {
  const { rows, state } = useAssessments();
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <Card className="gap-3 py-4">
      <CardHeader className="px-4">
        <CardTitle className="flex items-center gap-1.5 text-sm">
          <FileSearch className="size-4 text-muted-foreground" />
          Clinical decisions
        </CardTitle>
        <CardDescription className="text-xs">
          Every assessment this hospital made, newest first — read from{" "}
          <span className="font-mono">assessment_log</span>, not from the demo data below. It holds
          no patient identifier by design. Open one to see the arithmetic behind it.
        </CardDescription>
      </CardHeader>
      <CardContent className="px-4">
        {state === "unauthenticated" && (
          <Callout tone="warning">
            Not signed in — the decision trail is real data behind a real permission.
          </Callout>
        )}
        {state === "forbidden" && (
          <Callout tone="warning">
            Your role cannot read clinical explanations.{" "}
            <span className="font-mono">profile:explain</span> is held by the pharmacist and
            physician roles.
          </Callout>
        )}
        {state === "unavailable" && (
          <Callout tone="critical">
            patient-profiling is unreachable. This is not an empty trail, it is an unknown one.
          </Callout>
        )}
        {state === "loading" && (
          <p className="py-6 text-center text-xs text-muted-foreground">Reading the trail&hellip;</p>
        )}

        {state === "ok" && rows?.length === 0 && (
          <p className="py-6 text-center text-xs text-muted-foreground">
            No assessments recorded yet. One is written every time an assessment runs.
          </p>
        )}

        {state === "ok" && !!rows?.length && (
          <ol className="flex flex-col divide-y">
            {rows.map((row: AssessmentRow) => (
              <li key={row.request_id} className="py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge
                    tone={VERDICT_TONE[row.verdict ?? ""] ?? "neutral"}
                    className="normal-case"
                  >
                    {row.verdict ?? "no verdict"}
                  </StatusBadge>
                  <span className="text-xs font-medium">{row.actor_id}</span>
                  <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                    {when(row.created_at)}
                  </span>
                  <span className="font-mono text-[11px] text-muted-foreground">
                    {row.drugs.length} drug{row.drugs.length === 1 ? "" : "s"}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto h-7 text-xs"
                    onClick={() => setOpenId(openId === row.request_id ? null : row.request_id)}
                  >
                    {openId === row.request_id ? "Hide basis" : "Why?"}
                  </Button>
                </div>
                {openId === row.request_id && (
                  <div className="mt-2 overflow-hidden rounded-md border">
                    <Explanation requestId={row.request_id} />
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
