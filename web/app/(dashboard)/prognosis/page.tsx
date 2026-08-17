"use client";

import { Fragment, useState } from "react";
import { toast } from "sonner";
import { ClipboardList, FileText, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Callout } from "@/components/dashboard/Callout";
import { ReviewGate } from "@/components/dashboard/ReviewGate";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { useSession } from "@/lib/session";
import {
  canApprove,
  reviewProfile,
  useRiskProfileQueue,
  type ProfileStatus,
  type RiskFactor,
  type RiskProfile,
} from "@/lib/prognosis";

/**
 * PP-5 — the pharmacist's review queue for AI-extracted risk profiles.
 *
 * A model reads an FDA label and proposes "these patient characteristics raise
 * the risk of this reaction". Nothing it proposes reaches a patient-facing
 * screen until a pharmacist rules on it here
 * (docs/prognosis-and-procurement.md §1.3, gate 3). The page therefore has to
 * show the *basis* — the risk factors and the verbatim quote they came from —
 * not just the claim. A queue that asked for a signature on a drug name and a
 * reaction would be asking for a signature on nothing, and it is precisely the
 * reviewable basis that the FDA CDS exclusion turns on (§6 of the use-cases doc).
 *
 * Everything on this page is real: it comes from `patient-profiling`, or it
 * says why it does not. No mock profiles — a fabricated risk factor is the one
 * thing this screen must never show.
 */

const FILTERS: { value: string; label: string }[] = [
  { value: "awaiting_approval", label: "Awaiting review" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "all", label: "All profiles" },
];

const STATUS_TONE: Record<ProfileStatus, StatusTone> = {
  awaiting_approval: "warning",
  approved: "normal",
  // Not `critical`. A rejection is the gate working, not an alarm.
  rejected: "neutral",
};

const STATUS_LABEL: Record<ProfileStatus, string> = {
  awaiting_approval: "Awaiting",
  approved: "Approved",
  rejected: "Rejected",
};

// Seriousness drives the weight a matched profile contributes, so it belongs on
// the row a pharmacist rules from.
const SERIOUSNESS_TONE: Record<string, StatusTone> = {
  fatal: "critical",
  serious: "warning",
  moderate: "neutral",
  mild: "neutral",
};

function factorText(f: RiskFactor) {
  const value = Array.isArray(f.value) ? f.value.join(", ") : f.value;
  return `${f.feature} ${f.op.replace(/_/g, " ")} ${value}`;
}

function formatDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** The basis, spelled out. Opened from a row; this is what is being signed. */
function ProfileDetail({
  profile,
  mayApprove,
  onRuled,
}: {
  profile: RiskProfile;
  mayApprove: boolean;
  onRuled: () => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function rule(action: "approve" | "reject") {
    setBusy(true);
    try {
      await reviewProfile(profile.id, action, note);
      toast.success(
        `${profile.reaction} · ${action === "approve" ? "approved" : "rejected"} — recorded against your account.`,
      );
      setNote("");
      onRuled();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The ruling was not recorded.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 bg-muted/40 px-4 py-3">
      <div>
        <p className="mb-1.5 text-xs font-medium">Risk factors ({profile.risk_factors.length})</p>
        {profile.risk_factors.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            None extracted — nothing here could ever match a patient.
          </p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {profile.risk_factors.map((f, i) => (
              <li
                key={`${f.feature}-${i}`}
                className="rounded-md border bg-background px-1.5 py-0.5 font-mono text-[11px]"
              >
                {factorText(f)}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          Every feature above is a field of the de-identified vector. A factor naming anything else
          is rejected before it is written, because we could never evaluate it.
        </p>
      </div>

      <div>
        <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium">
          <FileText className="size-3.5 text-muted-foreground" />
          Cited label text
          {profile.section && <span className="font-normal text-muted-foreground">· {profile.section}</span>}
        </p>
        {profile.citation ? (
          <blockquote className="border-l-2 pl-2.5 text-xs italic text-muted-foreground">
            {profile.citation}
          </blockquote>
        ) : (
          <p className="text-xs text-muted-foreground">No quote — reject this one.</p>
        )}
        <p className="mt-1.5 font-mono text-[11px] text-muted-foreground">
          SPL {profile.spl_id ?? "unknown"} · extracted {formatDate(profile.extracted_at)}
        </p>
      </div>

      {profile.reviewed_by && (
        <p className="text-[11px] text-muted-foreground">
          Last ruled {STATUS_LABEL[profile.status].toLowerCase()} by{" "}
          <span className="font-medium text-foreground">{profile.reviewed_by}</span> on{" "}
          {formatDate(profile.reviewed_at)}
          {profile.review_note && <> — &ldquo;{profile.review_note}&rdquo;</>}
        </p>
      )}

      {mayApprove ? (
        <div className="flex flex-col gap-2">
          <Textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why — required reading for a rejection, so the next extraction is not re-reviewed from scratch."
            className="min-h-16 text-xs"
          />
          <div className="flex gap-2">
            <Button size="sm" className="h-8 text-xs" disabled={busy} onClick={() => rule("approve")}>
              Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-8 text-xs"
              disabled={busy}
              onClick={() => rule("reject")}
            >
              Reject
            </Button>
            {profile.status === "approved" && (
              <span className="self-center text-[11px] text-muted-foreground">
                Rejecting withdraws the approval — it stops colouring screens immediately.
              </span>
            )}
          </div>
        </div>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          Ruling on a profile is a clinical judgement and is restricted to the pharmacist role. You
          can read the queue and the accept rate.
        </p>
      )}
    </div>
  );
}

export default function PrognosisPage() {
  const { user } = useSession();
  const [status, setStatus] = useState("awaiting_approval");
  const [openId, setOpenId] = useState<number | null>(null);
  const { queue, state, refreshing, refresh } = useRiskProfileQueue(status);
  const mayApprove = canApprove(user?.role);

  const items = queue?.items ?? [];

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Prognosis Review</h1>
          <p className="text-xs text-muted-foreground">
            Label-derived risk profiles awaiting a pharmacist&apos;s ruling. Nothing here reaches an
            assessment or a forecast until it is approved.
          </p>
        </div>
        {/* One filter row above everything it scopes — the gate, the rate and
            the table all read the same slice. */}
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger size="sm" className="h-8 w-48 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {FILTERS.map((f) => (
                <SelectItem key={f.value} value={f.value}>
                  {f.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </div>

      {state === "unauthenticated" && (
        <Callout tone="warning">
          Not signed in. The review queue is real data behind a real permission — sign in as a
          pharmacist to rule on profiles, or as a director to read the accept rate.
        </Callout>
      )}
      {state === "forbidden" && (
        <Callout tone="warning">
          Your role cannot read the review queue. <span className="font-mono">profile:review</span>{" "}
          is held by the pharmacist, director and admin roles.
        </Callout>
      )}
      {state === "unavailable" && (
        <Callout tone="critical">
          <span className="font-medium">patient-profiling is unreachable.</span> This is not an
          empty queue — the backlog below is unknown, not clear. Nothing is shown rather than
          showing zero, because a reviewer reading &ldquo;nothing to review&rdquo; would close the
          page.
        </Callout>
      )}

      {/* Only once real counts have arrived. Rendering it during the first load
          would draw three zeroes and an empty gate, which asserts "nothing has
          been extracted" before anyone knows — the same lie as an empty queue.
          A later failed refresh keeps the last real numbers on screen, with the
          callout above saying they may be stale. */}
      {queue !== null && (
        <ReviewGate counts={queue.counts} acceptRate={queue.accept_rate} muted={refreshing} />
      )}

      <Card className="gap-0 py-0">
        <CardContent className="px-0">
          {state !== "ok" && queue === null ? (
            <p className="py-10 text-center text-xs text-muted-foreground">
              {state === "loading" ? "Reading the queue…" : "Queue not shown — see above."}
            </p>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center gap-1.5 py-10 text-center">
              <ClipboardList className="size-5 text-muted-foreground" />
              <p className="text-xs text-muted-foreground">
                {status === "awaiting_approval"
                  ? "Nothing awaiting review."
                  : `No ${FILTERS.find((f) => f.value === status)?.label.toLowerCase()}.`}
              </p>
              {queue !== null && queue.counts.awaiting_approval === 0 && queue.counts.approved === 0 && queue.counts.rejected === 0 && (
                <p className="max-w-md text-[11px] text-muted-foreground">
                  No profiles have been extracted yet. The extraction job runs offline in{" "}
                  <span className="font-mono">ingest</span>, per drug, against the formulary.
                </p>
              )}
            </div>
          ) : (
            /* This table is also the gate bar's table view: every count above is
               readable here as rows, not only as colour. */
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs">Drug</TableHead>
                  <TableHead className="text-xs">Reaction</TableHead>
                  <TableHead className="text-xs">Seriousness</TableHead>
                  <TableHead className="text-xs">Factors</TableHead>
                  <TableHead className="text-xs">Extracted</TableHead>
                  <TableHead className="text-xs">Status</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((profile) => (
                  <Fragment key={profile.id}>
                    <TableRow>
                      <TableCell className="font-mono text-xs tabular-nums">{profile.rxcui}</TableCell>
                      <TableCell className="text-xs">{profile.reaction}</TableCell>
                      <TableCell>
                        <StatusBadge tone={SERIOUSNESS_TONE[profile.seriousness] ?? "neutral"} className="normal-case">
                          {profile.seriousness}
                        </StatusBadge>
                      </TableCell>
                      <TableCell className="font-mono text-xs tabular-nums">
                        {profile.risk_factors.length}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatDate(profile.extracted_at)}
                      </TableCell>
                      <TableCell>
                        <StatusBadge tone={STATUS_TONE[profile.status]} className="normal-case">
                          {STATUS_LABEL[profile.status]}
                        </StatusBadge>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 gap-1.5 text-xs"
                          onClick={() => setOpenId(openId === profile.id ? null : profile.id)}
                        >
                          <ShieldAlert className="size-3.5" />
                          {openId === profile.id ? "Hide basis" : "Review basis"}
                        </Button>
                      </TableCell>
                    </TableRow>
                    {openId === profile.id && (
                      <TableRow>
                        <TableCell colSpan={7} className="p-0">
                          <ProfileDetail profile={profile} mayApprove={mayApprove} onRuled={refresh} />
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
