"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Copy,
  Eraser,
  FileText,
  History,
  Loader2,
  Plane,
  Plus,
  Repeat2,
  ShieldCheck,
  Send,
  Siren,
  Truck,
  X,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { StatusBadge } from "@/components/dashboard/StatusBadge";
import { useCopilot } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { useOrders } from "@/lib/orders-context";
import { useSession } from "@/lib/session";
import { useMediaQuery } from "@/lib/use-media-query";
import { apiFetch, streamCopilotMessage, type PatientCandidate } from "@/lib/api";
import type { CertStatus } from "@/components/CertificationBadge";
import { cn } from "@/lib/utils";

export type ResponseCard =
  | {
      kind: "po";
      itemId: string;
      ndc: string;
      drugName: string;
      supplier: string;
      quantity: number;
      unit: string;
      unitCost: number;
      totalCost: number;
      coverageDays: number;
      leadTimeDays: number;
      confidence: number;
      payload: Record<string, unknown>;
    }
  | {
      kind: "emergency";
      drugName: string;
      surgePct: number;
      depletionDays: number | null;
      airFreightDays: number;
      costPremiumPct: number;
    }
  | {
      kind: "analogues";
      tool?: string;
      request_id?: string;
      query_rxcui?: string;
      query_name?: string;
      items: {
        rxcui?: string;
        name: string;
        matchScore?: number;
        stockHere?: number;
        quantity?: number;
        in_stock?: boolean;
        ndcs?: string[];
        primary_ndc?: string;
      }[];
      truncated?: boolean;
    }
  | {
      kind: "certificate";
      tool?: string;
      request_id?: string;
      ndc: string;
      name?: string;
      status: CertStatus | string;
      codes?: string[];
      reasons?: string[] | number;
      transient?: number;
      persistent?: number;
      sources_consulted?: Record<string, boolean>;
      ruleset_version?: string;
      findings?: Array<{ code: string; severity: string; message: string }>;
    }
  | {
      kind: "sweep";
      tool?: string;
      request_id?: string;
      coverage?: { checked: number; total?: number; window?: string; source_note?: string };
      status_filter?: string;
      checked: number;
      flagged: Array<{
        ndc: string;
        name?: string;
        status: string;
        quantity: number;
        reasons?: string[];
        codes?: string[];
      }>;
      unknown: string[];
      by_facility?: Record<string, Array<{ ndc: string; name?: string; status: string; quantity: number; reasons?: string[]; codes?: string[] }>>;
      hospital_total?: { flagged_count: number; unknown_count: number; total_quantity: number };
      truncated?: boolean;
    }
  | {
      kind: "stock";
      tool?: string;
      request_id?: string;
      ndc?: string;
      rxcui?: string;
      name?: string;
      total_quantity: number;
      locations: Array<{ location_id: string; quantity: number; updated_at: string }>;
    }
  | {
      kind: "excursions";
      tool?: string;
      request_id?: string;
      coverage?: { checked: number; total?: number; window?: string; source_note?: string };
      facility_id?: string | number;
      window_hours?: number;
      checked: number;
      excursions: Array<{
        facility_id?: number | string;
        location_id: string;
        location_name?: string;
        temperature?: number;
        humidity?: number;
        min_temp?: number;
        max_temp?: number;
        breach_duration_hours?: number;
        stock_affected?: Array<{ ndc: string; drug_name?: string; quantity: number }>;
      }>;
      locations_monitored: number;
      locations_reporting: number;
      readings_checked: number;
      truncated?: boolean;
    }
  | {
      kind: "at_risk";
      tool?: string;
      request_id?: string;
      coverage?: { checked: number; total?: number; window?: string; source_note?: string };
      facility_id?: string | number;
      within_days: number;
      surge_pct: number;
      run_id?: string | null;
      data_through?: string | null;
      skus_evaluated: number;
      checked: number;
      items: Array<{
        ndc: string;
        name: string;
        rxcui?: string;
        stock: number;
        depletion_days?: number | null;
        burn_rate?: number;
        status?: string;
      }>;
      truncated?: boolean;
      note?: string | null;
    }
  | {
      kind: "patient_regimen";
      tool?: string;
      request_id?: string;
      age_band?: string;
      blood_group?: string;
      allergy_codes: string[];
      condition_codes: string[];
      pgx_phenotypes: string[];
    }
  | {
      kind: "safety_assessment";
      tool?: string;
      request_id?: string;
      patient_ref?: string;
      rxcui: string;
      drug_name?: string;
      verdict: string;
      hard_stop: boolean;
      score: number;
      findings: Array<{
        code: string;
        severity: string;
        message: string;
        category?: string;
        weight?: number;
      }>;
      stock_available?: number | null;
      cert_status?: string | null;
    }
  | {
      kind: "assessment_explain";
      tool?: string;
      request_id?: string;
      assessment_request_id: string;
      overall_score: number;
      verdict: string;
      contributions: Array<{
        code: string;
        severity: string;
        weight: number;
        share_pct?: number;
        message?: string;
      }>;
      ruleset_version?: string;
    }
  | {
      kind: "forecast";
      tool?: string;
      request_id?: string;
      ndc: string;
      run_id?: string | null;
      model_version?: string | null;
      points: Array<{ date: string; p50: number }>;
    }
  | {
      kind: "forecast_staleness";
      tool?: string;
      request_id?: string;
      has_run: boolean;
      run_id?: string | null;
      data_through?: string | null;
      generated_at?: string | null;
      note?: string | null;
    }
  | {
      kind: "review_queue";
      tool?: string;
      request_id?: string;
      coverage?: { checked: number; total?: number; window?: string; source_note?: string };
      status: string;
      queue_total: number;
      counts: Record<string, number>;
      accept_rate?: number | null;
      most_urgent: Array<{
        rxcui: string;
        reaction: string;
        seriousness: string;
        citation: string;
      }>;
    }
  | {
      kind: "audit_summary";
      tool?: string;
      request_id?: string;
      window_days?: number;
      total?: number;
      by_outcome?: Record<string, number>;
      top_tools?: Array<[string, number]>;
      latency_ms?: { p50: number; p95: number };
      error_rate?: number | null;
      recent?: Array<Record<string, unknown>>;
      single_record?: Record<string, unknown>;
    }
  | {
      kind: "drug_search";
      tool?: string;
      request_id?: string;
      query_name: string;
      matches: Array<{
        rxcui: string;
        ndc: string;
        name: string;
        on_hand: number;
      }>;
      ambiguous: boolean;
    }
  | {
      kind: "proposal";
      tool?: string;
      request_id?: string;
      proposal_id: string;
      action: string;
      facility_id: number;
      supplier_id: number;
      supplier_name?: string;
      ndc: string;
      drug_name?: string;
      quantity: number;
      unit?: string;
      pack_size?: number;
      est_total_cost?: number;
      coverage_days?: number;
      lead_time_days?: number;
      review_decision_id: number;
      review_decision_valid?: boolean;
      review_decision_note?: string;
      compliance_status: string;
      compliance_codes?: string[];
      blocked?: boolean;
      block_reason?: string;
      expires_at?: string;
    }
  | {
      kind: "run_proposal";
      tool?: string;
      request_id?: string;
      proposal_id: string;
      action: string;
      facility_id?: string | number;
      last_run_at?: string;
      expires_at?: string;
    };

export type ToolActivity = { name: string; status: "running" | "done" | "error"; error?: string };

export type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  card?: ResponseCard;
  cards?: ResponseCard[];
  live?: boolean;
  degraded?: boolean;
  tools?: ToolActivity[];
  patientPicker?: { query: string; candidates: PatientCandidate[] };
};

let nextId = 1;
function id() {
  return `m-${nextId++}`;
}

export type SavedConversation = { id: string; savedAt: number; title?: string | null; messages: Message[] };

const GREETING: Message = {
  id: "m-greeting",
  role: "assistant",
  text: "Hi, I'm the AI MedStock Assistant. Select a SKU, patient, or alert on the page, or ask me anything about safety, stock, and shortages.",
};

export type QuickAction = {
  key: string;
  label: string;
  icon: typeof FileText;
  prompt?: string;
};

const SHORTAGE_BRIEF_PROMPT =
  "For the drug currently in context: report on-hand stock by location, then find " +
  "substitutes ranked by what we hold, then check the compliance status of the top " +
  "candidate. Say plainly if any step returns nothing.";

export const ROLE_ACTIONS: Record<string, QuickAction[]> = {
  physician: [
    {
      key: "doc_safety",
      label: "Safety & Stock Check",
      icon: ShieldCheck,
      prompt:
        "For the current patient and drug in context: run the deterministic safety rules (allergies, duplicate ingredients, renal/hepatic, PGx) and check our on-hand physical stock. If blocked or short, suggest alternatives.",
    },
    {
      key: "doc_regimen",
      label: "Patient Regimen",
      icon: FileText,
      prompt:
        "Summarise the clinical profile (allergies, conditions, and PGx phenotypes) of the patient currently in context.",
    },
    {
      key: "doc_explain",
      label: "Explain Verdict",
      icon: Sparkles,
      prompt:
        "Explain the deterministic score contributions and findings of the most recent safety assessment for this patient.",
    },
    {
      key: "analogue",
      label: "Find Alternatives",
      icon: Repeat2,
      prompt:
        "Find safe therapeutic alternatives in stock for the drug currently in context.",
    },
  ],
  pharmacist: [
    { key: "shortage", label: "Shortage Brief", icon: Siren, prompt: SHORTAGE_BRIEF_PROMPT },
    {
      key: "sweep",
      label: "Shelf Cert Sweep",
      icon: ShieldCheck,
      prompt:
        "Review the compliance status of every stocked NDC in our hospital and report any red or yellow items needing attention.",
    },
    {
      key: "excursions",
      label: "Cold-Chain Excursions",
      icon: AlertTriangle,
      prompt:
        "Report any storage condition violations or temperature excursions recorded in telemetry.",
    },
    { key: "analogue", label: "Find Bio-Equivalent", icon: Repeat2 },
  ],
  admin: [
    {
      key: "sweep",
      label: "Shelf Cert Sweep",
      icon: ShieldCheck,
      prompt:
        "Review compliance status of stocked NDCs to ensure no recalled or non-compliant drugs are reordered.",
    },
    {
      key: "excursions",
      label: "Storage Report",
      icon: AlertTriangle,
      prompt:
        "Report storage condition excursions across facilities before new shipments arrive.",
    },
    { key: "po", label: "Generate PO", icon: FileText },
    { key: "analogue", label: "Find Bio-Equivalent", icon: Repeat2 },
  ],
  director: [
    {
      key: "risk_digest",
      label: "Facility Risk Digest",
      icon: AlertTriangle,
      prompt:
        "Provide a cross-facility risk digest: at-risk SKUs depleting soon, storage excursions, and non-compliant certificates.",
    },
    {
      key: "forecast_staleness",
      label: "Forecast Staleness",
      icon: Sparkles,
      prompt:
        "Check whether this hospital's forecast is stale and report the timestamp of the last forecast run.",
    },
    {
      key: "review_queue",
      label: "Review Queue",
      icon: FileText,
      prompt:
        "Summarise the label risk-profile review queue: how many are awaiting approval, accept rate, and urgent pending items.",
    },
    {
      key: "audit",
      label: "AI Decisions",
      icon: History,
      prompt:
        "Summarise this hospital's AI-assisted decisions over the last 30 days.",
    },
  ],
};

function ComplianceChip({ status, codes }: { status?: string; codes?: string[] }) {
  const s = (status ?? "unknown").toLowerCase();
  if (s === "green") {
    return <Badge className="bg-emerald-600/15 text-emerald-700 hover:bg-emerald-600/20 dark:text-emerald-400">● Certified</Badge>;
  }
  if (s === "yellow") {
    return <Badge className="bg-amber-600/15 text-amber-700 hover:bg-amber-600/20 dark:text-amber-400">● Warning {codes?.length ? `(${codes.join(", ")})` : ""}</Badge>;
  }
  if (s === "red") {
    return <Badge variant="destructive">● Blocked {codes?.length ? `(${codes.join(", ")})` : ""}</Badge>;
  }
  return <Badge variant="outline" className="border-dashed text-muted-foreground">○ Unknown</Badge>;
}

function CoverageLine({ coverage }: { coverage?: { checked: number; total?: number; window?: string; source_note?: string } }) {
  if (!coverage) return null;
  const isClean = coverage.checked > 0 && !coverage.source_note;
  if (isClean) {
    return (
      <div className="flex items-center gap-1.5 text-[11px] text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="size-3 shrink-0" />
        <span>{coverage.checked} {coverage.window ? `in ${coverage.window}` : ""} checked · all within range</span>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-1.5 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-400">
      <AlertTriangle className="mt-0.5 size-3 shrink-0" />
      <span>{coverage.source_note ?? "No readings or records measured — this is not a clean result."}</span>
    </div>
  );
}

function TruncationBanner({ truncated }: { truncated?: boolean }) {
  if (!truncated) return null;
  return (
    <div className="rounded bg-muted/60 px-2 py-1 text-[10px] text-muted-foreground">
      Results truncated to top entries.
    </div>
  );
}

function ProvenanceFooter({ tool, requestId }: { tool?: string; requestId?: string }) {
  const [expanded, setExpanded] = useState(false);
  if (!tool && !requestId) return null;
  return (
    <div className="mt-1 border-t pt-1 text-[10px] text-muted-foreground">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between hover:text-foreground"
      >
        <span>Source: {tool ?? "deterministic engine"}</span>
        <span>{expanded ? "▾ hide" : "▸ provenance"}</span>
      </button>
      {expanded && (
        <div className="mt-1 space-y-0.5 font-mono text-[9px]">
          {requestId && <div>request_id: {requestId}</div>}
          {tool && <div>tool_name: {tool}</div>}
        </div>
      )}
    </div>
  );
}

function formatCardForCopy(card: ResponseCard): string {
  if (card.kind === "po") {
    return `Draft Purchase Order — ${card.drugName}\nSupplier: ${card.supplier}\nQuantity: ${card.quantity} ${card.unit}\nCoverage: ${card.coverageDays} days\nEst. total: $${card.totalCost.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  }
  if (card.kind === "proposal") {
    return `Draft PO Proposal — ${card.drug_name || card.ndc}\nSupplier: ${card.supplier_name}\nQuantity: ${card.quantity} ${card.unit}\nEst. Total: $${card.est_total_cost?.toFixed(2)}\nStatus: ${card.blocked ? `Blocked (${card.block_reason})` : "Pending Confirmation"}`;
  }
  if (card.kind === "analogues") {
    return ["Bio-Equivalent Analogues", ...card.items.map((it) => `${it.name} — ${it.matchScore ?? 100}% match — ${(it.quantity ?? it.stockHere ?? 0) > 0 ? `${it.quantity ?? it.stockHere} in stock` : "not stocked here"}`)].join("\n");
  }
  if (card.kind === "certificate") {
    return `Certificate Status\nNDC: ${card.ndc}\nStatus: ${card.status}\nFindings: ${card.reasons || (card.codes ? card.codes.join(", ") : "None")}`;
  }
  if (card.kind === "safety_assessment") {
    return `Safety Assessment — ${card.drug_name || card.rxcui}\nVerdict: ${card.verdict.toUpperCase()}\nFindings:\n` + (card.findings || []).map((f) => ` - [${f.severity}] ${f.message}`).join("\n");
  }
  return JSON.stringify(card, null, 2);
}

function ResponseCardView({
  card,
  onCreateDraft,
  onConfirmProposal,
  onAskFollowup,
  drafted,
}: {
  card: ResponseCard;
  onCreateDraft?: () => void;
  onConfirmProposal?: (proposal: Extract<ResponseCard, { kind: "proposal" }>) => void;
  onAskFollowup?: (prompt: string) => void;
  drafted?: boolean;
}) {
  if (card.kind === "po") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3">
          <CardTitle className="text-xs font-medium text-muted-foreground">Draft Purchase Order</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-1 px-3 text-sm">
          <div className="flex justify-between"><span className="text-muted-foreground">Supplier</span><span>{card.supplier}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Quantity</span><span>{card.quantity} {card.unit}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Coverage</span><span>{card.coverageDays} days</span></div>
          <div className="flex justify-between font-medium"><span className="text-muted-foreground font-normal">Est. total</span><span>${card.totalCost.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span></div>
        </CardContent>
        {onCreateDraft && (
          <CardContent className="px-3 pt-1">
            <Button size="sm" className="h-7 w-full text-xs" disabled={drafted} onClick={onCreateDraft}>
              {drafted ? <CheckCircle2 data-icon="inline-start" /> : <Truck data-icon="inline-start" />}
              {drafted ? "Draft created" : "Create Draft Order"}
            </Button>
          </CardContent>
        )}
      </Card>
    );
  }

  if (card.kind === "proposal") {
    return (
      <Card className={cn("gap-2 py-3", card.blocked ? "border-red-500/40" : "border-primary/40")}>
        <CardHeader className="px-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-xs font-semibold">
              {card.blocked ? "⛔ Order Proposal (Blocked)" : "Draft Purchase Order — Needs Confirmation"}
            </CardTitle>
            <ComplianceChip status={card.compliance_status} codes={card.compliance_codes} />
          </div>
        </CardHeader>
        <CardContent className="grid gap-1.5 px-3 text-sm">
          <div className="flex justify-between"><span className="text-muted-foreground">Drug</span><span className="font-medium">{card.drug_name || card.ndc}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">NDC</span><span className="font-mono text-xs">{card.ndc}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Quantity</span><span className="tabular-nums">{card.quantity} {card.unit} ({Math.ceil(card.quantity / (card.pack_size || 1))} packs)</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Supplier</span><span>{card.supplier_name} {card.lead_time_days ? `· ${card.lead_time_days}d lead` : ""}</span></div>
          {card.est_total_cost && (
            <div className="flex justify-between font-medium"><span className="text-muted-foreground font-normal">Est. total</span><span>${card.est_total_cost.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span></div>
          )}

          {card.review_decision_note && (
            <div className="mt-1 rounded bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
              <span className="font-medium">⚠ Review Decision: </span>{card.review_decision_note}
            </div>
          )}

          {card.blocked && card.block_reason && (
            <div className="mt-1 rounded bg-destructive/10 p-2 text-xs text-destructive">
              <span className="font-medium">⛔ Blocked: </span>{card.block_reason}
            </div>
          )}
        </CardContent>
        <CardContent className="flex items-center gap-2 px-3 pt-1">
          <Button
            size="sm"
            className="h-7 flex-1 text-xs"
            disabled={card.blocked || drafted}
            onClick={() => onConfirmProposal?.(card)}
          >
            {drafted ? <CheckCircle2 className="size-3" /> : <Truck className="size-3" />}
            {drafted ? "Draft Confirmed" : "Confirm draft"}
          </Button>
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "safety_assessment") {
    const isHardStop = card.hard_stop || card.verdict.toLowerCase().includes("block") || card.verdict.toLowerCase().includes("fail");
    const isCaution = card.verdict.toLowerCase().includes("caution") || card.verdict.toLowerCase().includes("warn");
    return (
      <Card className={cn("gap-2 py-3", isHardStop ? "border-red-500/50 bg-red-500/5" : isCaution ? "border-amber-500/50 bg-amber-500/5" : "border-emerald-500/50 bg-emerald-500/5")}>
        <CardHeader className="px-3 pb-1">
          <div className="flex items-center justify-between">
            <CardTitle className="text-xs font-semibold">
              {isHardStop ? "⛔ DO NOT PRESCRIBE" : isCaution ? "⚠ PRESCRIBE WITH CAUTION" : "✓ PRESCRIBE (CLEAR)"}
            </CardTitle>
            <Badge variant={isHardStop ? "destructive" : isCaution ? "secondary" : "outline"}>
              {card.verdict.toUpperCase()}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">{card.drug_name || `RxCUI ${card.rxcui}`} · Score: {card.score}</p>
        </CardHeader>
        <CardContent className="grid gap-1.5 px-3 text-xs">
          {card.findings?.map((f, i) => (
            <div key={i} className="flex items-start gap-1.5">
              <span className={cn("font-semibold uppercase", f.severity === "fatal" || f.severity === "high" ? "text-destructive" : "text-amber-600 dark:text-amber-400")}>
                [{f.severity}]
              </span>
              <span>{f.message}</span>
            </div>
          ))}
          {card.stock_available !== undefined && (
            <div className="mt-1 flex justify-between border-t pt-1">
              <span className="text-muted-foreground">On-hand availability:</span>
              <span className="font-medium">{card.stock_available ?? 0} units</span>
            </div>
          )}
        </CardContent>
        {onAskFollowup && (
          <CardContent className="flex flex-wrap gap-1 px-3 pt-1">
            <Button variant="outline" size="sm" className="h-6 text-[11px]" onClick={() => onAskFollowup("Why was this flagged?")}>
              Why was this flagged?
            </Button>
            <Button variant="outline" size="sm" className="h-6 text-[11px]" onClick={() => onAskFollowup("What can I give instead?")}>
              What can I give instead?
            </Button>
          </CardContent>
        )}
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "assessment_explain") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <CardTitle className="text-xs font-medium text-muted-foreground">Assessment Explanation</CardTitle>
          <p className="text-xs">Overall Score: <span className="font-mono font-bold">{card.overall_score}</span> ({card.verdict})</p>
        </CardHeader>
        <CardContent className="grid gap-1.5 px-3 text-xs">
          {card.contributions?.map((c, i) => (
            <div key={i} className="space-y-0.5 border-b pb-1 last:border-0">
              <div className="flex justify-between font-medium">
                <span>{c.code}</span>
                <span className="font-mono">Weight: {c.weight}</span>
              </div>
              {c.message && <p className="text-muted-foreground">{c.message}</p>}
            </div>
          ))}
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "analogues") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <CardTitle className="text-xs font-medium text-muted-foreground">Bio-Equivalent Analogues</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 px-3 text-sm">
          {card.items.map((it, idx) => (
            <div key={it.name || idx} className="flex items-center justify-between gap-2 border-b pb-1.5 last:border-0 last:pb-0">
              <div className="min-w-0">
                <p className="truncate text-xs font-medium">{it.name}</p>
                <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  <span>{it.matchScore ?? 100}% match</span>
                  {it.primary_ndc && <span>· NDC {it.primary_ndc}</span>}
                </div>
              </div>
              <Badge variant={(it.quantity ?? it.stockHere ?? 0) > 0 ? "secondary" : "outline"} className="shrink-0 text-xs">
                {(it.quantity ?? it.stockHere ?? 0) > 0 ? `${it.quantity ?? it.stockHere} in stock` : "Not stocked"}
              </Badge>
            </div>
          ))}
          <TruncationBanner truncated={card.truncated} />
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "sweep") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <div className="flex items-center justify-between">
            <CardTitle className="text-xs font-medium text-muted-foreground">Shelf Certificate Sweep</CardTitle>
            <Badge variant="outline" className="text-[10px]">{card.checked} checked</Badge>
          </div>
          <CoverageLine coverage={card.coverage} />
        </CardHeader>
        <CardContent className="grid gap-2 px-3 text-xs">
          {card.flagged?.slice(0, 5).map((f) => (
            <div key={f.ndc} className="flex items-start justify-between gap-2 border-b pb-1.5 last:border-0">
              <div>
                <p className="font-medium">{f.name || f.ndc}</p>
                <p className="font-mono text-[10px] text-muted-foreground">{f.ndc}</p>
                {f.reasons?.length ? <p className="text-[10px] text-amber-600 dark:text-amber-400">{f.reasons.join(", ")}</p> : null}
              </div>
              <div className="text-right">
                <ComplianceChip status={f.status} codes={f.codes} />
                <p className="text-[10px] text-muted-foreground">{f.quantity} on hand</p>
              </div>
            </div>
          ))}
          {card.unknown?.length > 0 && (
            <p className="text-[11px] text-muted-foreground">+{card.unknown.length} NDCs with unknown certification status</p>
          )}
          <TruncationBanner truncated={card.truncated} />
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "stock") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <CardTitle className="text-xs font-medium text-muted-foreground">Stock On-Hand</CardTitle>
          <div className="flex justify-between text-sm font-semibold">
            <span>{card.name || card.ndc || card.rxcui}</span>
            <span className="font-mono">{card.total_quantity} units</span>
          </div>
        </CardHeader>
        <CardContent className="grid gap-1 px-3 text-xs">
          {card.locations?.map((loc, i) => (
            <div key={i} className="flex justify-between text-muted-foreground">
              <span>Location: {loc.location_id}</span>
              <span className="font-mono text-foreground">{loc.quantity} units</span>
            </div>
          ))}
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "excursions") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <div className="flex items-center justify-between">
            <CardTitle className="text-xs font-medium text-muted-foreground">Storage Excursions</CardTitle>
            <span className="text-[10px] text-muted-foreground">{card.window_hours}h window</span>
          </div>
          <CoverageLine coverage={card.coverage} />
        </CardHeader>
        <CardContent className="grid gap-1.5 px-3 text-xs">
          {card.excursions?.map((exc, i) => (
            <div key={i} className="rounded border border-red-500/30 bg-red-500/5 p-2">
              <div className="flex justify-between font-medium">
                <span>Location: {exc.location_name || exc.location_id}</span>
                <span className="text-destructive font-mono">{exc.temperature ? `${exc.temperature}°C` : ""}{exc.humidity ? ` ${exc.humidity}% RH` : ""}</span>
              </div>
              {exc.breach_duration_hours && (
                <p className="text-[10px] text-muted-foreground">Breach duration: {exc.breach_duration_hours} hours</p>
              )}
            </div>
          ))}
          <TruncationBanner truncated={card.truncated} />
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "at_risk") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <div className="flex items-center justify-between">
            <CardTitle className="text-xs font-medium text-muted-foreground">At-Risk Depleting SKUs</CardTitle>
            <span className="text-[10px] text-muted-foreground">Within {card.within_days}d</span>
          </div>
          <CoverageLine coverage={card.coverage} />
        </CardHeader>
        <CardContent className="grid gap-1.5 px-3 text-xs">
          {card.items?.slice(0, 5).map((sku) => (
            <div key={sku.ndc} className="flex justify-between border-b pb-1 last:border-0">
              <div>
                <p className="font-medium">{sku.name}</p>
                <p className="font-mono text-[10px] text-muted-foreground">{sku.ndc}</p>
              </div>
              <div className="text-right">
                <Badge variant={sku.depletion_days && sku.depletion_days <= 7 ? "destructive" : "secondary"}>
                  {sku.depletion_days != null ? `~${sku.depletion_days}d` : "Depleting"}
                </Badge>
                <p className="text-[10px] text-muted-foreground">{sku.stock} in stock</p>
              </div>
            </div>
          ))}
          <TruncationBanner truncated={card.truncated} />
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "patient_regimen") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <CardTitle className="text-xs font-medium text-muted-foreground">Patient Profile Snapshot</CardTitle>
          <div className="flex gap-2 text-xs">
            {card.age_band && <span>Age band: <strong className="font-semibold">{card.age_band}</strong></span>}
            {card.blood_group && <span>Blood group: <strong className="font-semibold">{card.blood_group}</strong></span>}
          </div>
        </CardHeader>
        <CardContent className="grid gap-2 px-3 text-xs">
          <div>
            <p className="text-[10px] text-muted-foreground uppercase font-semibold">Documented Allergies</p>
            <div className="flex flex-wrap gap-1 mt-0.5">
              {card.allergy_codes?.length ? card.allergy_codes.map((a) => <Badge key={a} variant="destructive" className="text-[10px]">{a}</Badge>) : <span className="text-muted-foreground">None documented</span>}
            </div>
          </div>
          <div>
            <p className="text-[10px] text-muted-foreground uppercase font-semibold">Conditions & PGx</p>
            <div className="flex flex-wrap gap-1 mt-0.5">
              {card.condition_codes?.map((c) => <Badge key={c} variant="secondary" className="text-[10px]">{c}</Badge>)}
              {card.pgx_phenotypes?.map((p) => <Badge key={p} variant="outline" className="text-[10px]">{p}</Badge>)}
            </div>
          </div>
          <p className="text-[10px] text-muted-foreground italic">Note: Active medication list is not stored here — verify on chart.</p>
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "certificate") {
    const findingsList = card.findings || [];
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-xs font-medium text-muted-foreground">Compliance Certification</CardTitle>
            <ComplianceChip status={String(card.status)} codes={card.codes} />
          </div>
          <p className="font-mono text-xs font-semibold">{card.ndc}</p>
        </CardHeader>
        <CardContent className="grid gap-1 px-3 text-xs">
          {findingsList.map((f, i) => (
            <div key={i} className="flex justify-between border-b pb-1 last:border-0">
              <span className="font-semibold text-destructive">[{f.code}]</span>
              <span>{f.message}</span>
            </div>
          ))}
          {card.sources_consulted && (
            <div className="mt-1 space-y-0.5 border-t pt-1 text-[10px] text-muted-foreground">
              {Object.entries(card.sources_consulted).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span>{k.replace(/_/g, " ")}</span>
                  <span>{v ? "✓ consulted" : "— not consulted"}</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "forecast") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <div className="flex items-center justify-between">
            <CardTitle className="text-xs font-medium text-muted-foreground">Demand Forecast Projection</CardTitle>
            {card.model_version && <Badge variant="outline" className="text-[10px]">{card.model_version}</Badge>}
          </div>
          <p className="font-mono text-xs">{card.ndc}</p>
        </CardHeader>
        <CardContent className="grid gap-1 px-3 text-xs">
          {card.points?.slice(0, 7).map((pt) => (
            <div key={pt.date} className="flex justify-between border-b pb-0.5 last:border-0">
              <span className="text-muted-foreground">{pt.date}</span>
              <span className="font-mono font-medium">{pt.p50} units</span>
            </div>
          ))}
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "forecast_staleness") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <div className="flex items-center justify-between">
            <CardTitle className="text-xs font-medium text-muted-foreground">Forecast Staleness</CardTitle>
            <Badge variant={card.has_run ? "secondary" : "destructive"}>
              {card.has_run ? "Active Model" : "No Forecast Run"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid gap-1 px-3 text-xs">
          {card.data_through && (
            <div className="flex justify-between"><span className="text-muted-foreground">Data through:</span><span>{card.data_through}</span></div>
          )}
          {card.generated_at && (
            <div className="flex justify-between"><span className="text-muted-foreground">Generated at:</span><span>{card.generated_at}</span></div>
          )}
          {card.note && <p className="mt-1 text-muted-foreground italic">{card.note}</p>}
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "review_queue") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <div className="flex items-center justify-between">
            <CardTitle className="text-xs font-medium text-muted-foreground">Label Review Queue</CardTitle>
            <Badge variant="secondary">{card.queue_total} total</Badge>
          </div>
          <CoverageLine coverage={card.coverage} />
        </CardHeader>
        <CardContent className="grid gap-1.5 px-3 text-xs">
          {card.accept_rate != null && (
            <div className="flex justify-between font-medium">
              <span className="text-muted-foreground">Accept rate:</span>
              <span>{(card.accept_rate * 100).toFixed(0)}%</span>
            </div>
          )}
          {card.most_urgent?.slice(0, 3).map((item, i) => (
            <div key={i} className="rounded border p-1.5 space-y-0.5">
              <div className="flex justify-between font-medium">
                <span>{item.reaction}</span>
                <Badge variant={item.seriousness === "fatal" || item.seriousness === "high" ? "destructive" : "outline"} className="text-[10px]">
                  {item.seriousness}
                </Badge>
              </div>
              <p className="text-[10px] text-muted-foreground truncate">{item.citation}</p>
            </div>
          ))}
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "audit_summary") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <div className="flex items-center justify-between">
            <CardTitle className="text-xs font-medium text-muted-foreground">AI Decision Audit</CardTitle>
            <Badge variant="outline">{card.total ?? 0} turns</Badge>
          </div>
          {card.window_days && <p className="text-[11px] text-muted-foreground">Last {card.window_days} days</p>}
        </CardHeader>
        <CardContent className="grid gap-1.5 px-3 text-xs">
          {card.latency_ms && (
            <div className="flex justify-between text-muted-foreground">
              <span>Latency (p50 / p95):</span>
              <span className="font-mono text-foreground">{card.latency_ms.p50}ms / {card.latency_ms.p95}ms</span>
            </div>
          )}
          {card.top_tools && (
            <div className="space-y-0.5 border-t pt-1">
              <p className="font-semibold text-[10px] text-muted-foreground uppercase">Top Tools Used</p>
              {card.top_tools.slice(0, 4).map(([tname, count]) => (
                <div key={tname} className="flex justify-between text-[11px]">
                  <span className="truncate">{tname}</span>
                  <span className="font-mono">{count}</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "drug_search") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <CardTitle className="text-xs font-medium text-muted-foreground">Drug Catalog Matches</CardTitle>
          <p className="text-xs">Query: &quot;{card.query_name}&quot;</p>
        </CardHeader>
        <CardContent className="grid gap-1 px-3 text-xs">
          {card.matches?.map((m) => (
            <div key={m.ndc || m.rxcui} className="flex items-center justify-between border-b pb-1 last:border-0">
              <div className="min-w-0">
                <p className="font-medium truncate">{m.name}</p>
                <p className="font-mono text-[10px] text-muted-foreground">NDC: {m.ndc} · RxCUI: {m.rxcui}</p>
              </div>
              <Badge variant={m.on_hand > 0 ? "secondary" : "outline"} className="shrink-0 text-[10px]">
                {m.on_hand > 0 ? `${m.on_hand} in stock` : "0 in stock"}
              </Badge>
            </div>
          ))}
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "run_proposal") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3 pb-1">
          <CardTitle className="text-xs font-medium text-muted-foreground">Proposed Action</CardTitle>
          <p className="text-xs font-medium">{card.action}</p>
        </CardHeader>
        <CardContent className="grid gap-1 px-3 text-xs text-muted-foreground">
          {card.facility_id && <div>Facility: #{card.facility_id}</div>}
          {card.last_run_at && <div>Last run: {card.last_run_at}</div>}
        </CardContent>
        <ProvenanceFooter tool={card.tool} requestId={card.request_id} />
      </Card>
    );
  }

  if (card.kind === "emergency") {
    return (
      <Card className="gap-2 border-red-500/30 py-3">
        <CardHeader className="px-3">
          <CardTitle className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <AlertTriangle className="size-3.5 text-red-500" />
            Emergency Supply Plan
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-1.5 px-3 text-sm">
          <div className="flex justify-between"><span className="text-muted-foreground">Scenario</span><span className="font-mono tabular-nums">{card.surgePct}% of baseline demand</span></div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Stock depletes in</span>
            <StatusBadge tone={card.depletionDays != null && card.depletionDays <= 5 ? "critical" : "warning"}>
              {card.depletionDays != null ? `${card.depletionDays}d` : "30d+"}
            </StatusBadge>
          </div>
          <div className="flex justify-between"><span className="text-muted-foreground">Freight mode</span><span className="flex items-center gap-1"><Plane className="size-3.5" /> Air (expedited)</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Lead time</span><span className="font-mono tabular-nums">{card.airFreightDays} days</span></div>
          <div className="flex justify-between font-medium"><span className="text-muted-foreground font-normal">Cost premium</span><span className="font-mono tabular-nums">+{card.costPremiumPct}%</span></div>
        </CardContent>
      </Card>
    );
  }

  return null;
}

function LiveText({ text }: { text: string }) {
  return <p className="whitespace-pre-wrap text-sm leading-relaxed">{text}</p>;
}

function ToolActivityRow({ tools }: { tools: ToolActivity[] }) {
  if (tools.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {tools.map((t, i) => (
        <Badge
          key={`${t.name}-${i}`}
          variant={t.status === "error" ? "destructive" : "secondary"}
          className="gap-1 text-[10px] font-normal"
          title={t.error}
        >
          {t.status === "running" && <Loader2 className="size-3 animate-spin" />}
          {t.status === "done" && <CheckCircle2 className="size-3" />}
          {t.status === "error" && <AlertTriangle className="size-3" />}
          {t.name}
        </Badge>
      ))}
    </div>
  );
}

function StreamingText({ text }: { text: string }) {
  const [shown, setShown] = useState("");
  useEffect(() => {
    setShown("");
    let i = 0;
    const t = setInterval(() => {
      i += 3;
      setShown(text.slice(0, i));
      if (i >= text.length) clearInterval(t);
    }, 15);
    return () => clearInterval(t);
  }, [text]);
  return (
    <>
      <p className="text-sm leading-relaxed" aria-hidden="true">{shown}</p>
      <p className="sr-only">{text}</p>
    </>
  );
}

export function CopilotDrawer() {
  const router = useRouter();
  const { user } = useSession();
  const { open, setOpen, focus, emergencyRequest } = useCopilot();
  const { facility } = useFacility();
  const { reload: reloadOrders } = useOrders();
  const role = user?.role ?? "pharmacist";
  const quickActions = ROLE_ACTIONS[role] ?? ROLE_ACTIONS.pharmacist;
  const isDesktop = useMediaQuery("(min-width: 1024px)");
  const [messages, setMessages] = useState<Message[]>([GREETING]);
  const [draft, setDraft] = useState("");
  const [history, setHistory] = useState<SavedConversation[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [draftedMessageIds, setDraftedMessageIds] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const lastHandledNonce = useRef<number | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  useEffect(() => () => streamAbortRef.current?.abort(), []);

  function mapApiMessages(
    items: { id: number; role: string; text: string | null; card?: ResponseCard | null; tool_name?: string | null }[],
  ): Message[] {
    const result: Message[] = [];
    for (const row of items) {
      if (row.role === "user") {
        result.push({ id: `api-${row.id}`, role: "user", text: row.text ?? "" });
      } else if (row.role === "tool" && row.card) {
        result.push({
          id: `api-${row.id}`,
          role: "assistant",
          text: "",
          card: row.card,
          live: true,
        });
      } else if (row.role === "assistant") {
        result.push({
          id: `api-${row.id}`,
          role: "assistant",
          text: row.text ?? "",
          card: row.card ?? undefined,
          live: true,
        });
      }
    }
    return result.length === 0 ? [GREETING] : result;
  }

  async function refreshHistory() {
    const body = (await apiFetch("copilot", "/conversations?limit=10")) as {
      items: { id: string; title: string | null; created_at: string | null }[];
    };
    setHistory(
      (body.items ?? []).map((row) => ({
        id: row.id,
        savedAt: row.created_at ? new Date(row.created_at).getTime() : 0,
        title: row.title,
        messages: [],
      })),
    );
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const body = (await apiFetch("copilot", "/conversations?limit=10")) as {
          items: { id: string; title: string | null; created_at: string | null }[];
        };
        if (cancelled) return;
        const items = body.items ?? [];
        setHistory(
          items.map((row) => ({
            id: row.id,
            savedAt: row.created_at ? new Date(row.created_at).getTime() : 0,
            title: row.title,
            messages: [],
          })),
        );
        if (!items[0]) return;
        const conv = (await apiFetch("copilot", `/conversations/${items[0].id}`)) as {
          id: string;
          items: { id: number; role: string; text: string | null; card?: ResponseCard | null; tool_name?: string | null }[];
        };
        if (cancelled) return;
        setConversationId(conv.id);
        setMessages(mapApiMessages(conv.items ?? []));
      } catch {
        /* stay on greeting if the gateway is down */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!emergencyRequest || emergencyRequest.nonce === lastHandledNonce.current) return;
    lastHandledNonce.current = emergencyRequest.nonce;
    setMessages((m) => [
      ...m,
      { id: id(), role: "user", text: `Generate emergency supply plan for current load — ${emergencyRequest.drugName}` },
    ]);
    setPending(true);
    window.setTimeout(() => {
      const costPremiumPct = emergencyRequest.surgePct >= 200 ? 45 : emergencyRequest.surgePct >= 150 ? 25 : 10;
      setMessages((m) => [
        ...m,
        {
          id: id(),
          role: "assistant",
          text:
            emergencyRequest.depletionDays != null
              ? `Emergency supply plan for ${emergencyRequest.drugName} at ${emergencyRequest.surgePct}% projected load — current stock depletes in ~${emergencyRequest.depletionDays} day${emergencyRequest.depletionDays === 1 ? "" : "s"}. Recommending expedited air freight.`
              : `Emergency supply plan for ${emergencyRequest.drugName} at ${emergencyRequest.surgePct}% projected load — stock holds beyond the 30-day forecast window.`,
          card: {
            kind: "emergency",
            drugName: emergencyRequest.drugName,
            surgePct: emergencyRequest.surgePct,
            depletionDays: emergencyRequest.depletionDays,
            airFreightDays: 2,
            costPremiumPct,
          },
        },
      ]);
      setPending(false);
    }, 300);
  }, [emergencyRequest]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  function runAction(actionKey: string) {
    if (pending) return;
    const action = quickActions.find((a) => a.key === actionKey);
    const label = action?.label ?? actionKey;
    setMessages((m) => [...m, { id: id(), role: "user", text: focus ? `${label} — ${focus.label}` : label }]);
    setPending(true);
    if (action?.prompt) {
      void streamReply(action.prompt);
      return;
    }
  }

  function clearConversation() {
    setMessages([GREETING]);
  }

  async function startNewConversation() {
    try {
      const created = (await apiFetch("copilot", "/conversations", {
        method: "POST",
        body: JSON.stringify({ facility_id: facility.id }),
      })) as { id: string };
      setConversationId(created.id);
      await refreshHistory();
    } catch {
      setConversationId(null);
    }
    setMessages([GREETING]);
    setHistoryOpen(false);
  }

  async function openConversation(saved: SavedConversation) {
    try {
      const conv = (await apiFetch("copilot", `/conversations/${saved.id}`)) as {
        id: string;
        items: { id: number; role: string; text: string | null; card?: ResponseCard | null; tool_name?: string | null }[];
      };
      setConversationId(conv.id);
      setMessages(mapApiMessages(conv.items ?? []));
    } catch {
      toast.error("Could not load that conversation.");
    }
    setHistoryOpen(false);
  }

  async function createDraftFromCard(cardKey: string, card: Extract<ResponseCard, { kind: "po" }>) {
    try {
      const rec = (await apiFetch("inventory", "/recommendations", {
        method: "POST",
        body: JSON.stringify({ facility_id: facility.id, payload: card.payload }),
      })) as { id: number };
      const order = (await apiFetch("inventory", `/recommendations/${rec.id}/approve`, {
        method: "POST",
      })) as { ref: string };
      reloadOrders();
      setDraftedMessageIds((prev) => new Set(prev).add(cardKey));
      toast.success(`Draft order ${order.ref} created.`, {
        description: `${card.quantity} ${card.unit} of ${card.drugName} for ${facility.name}.`,
        action: { label: "Review", onClick: () => router.push("/orders") },
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not create draft order.");
    }
  }

  async function confirmProposal(proposal: Extract<ResponseCard, { kind: "proposal" }>) {
    try {
      const confirmed = (await apiFetch("copilot", "/orders/confirm", {
        method: "POST",
        body: JSON.stringify({
          proposal_id: proposal.proposal_id,
          facility_id: proposal.facility_id,
          supplier_id: proposal.supplier_id,
          ndc: proposal.ndc,
          quantity: proposal.quantity,
          review_decision_id: proposal.review_decision_id,
        }),
      })) as { ref: string; id: number };
      reloadOrders();
      setDraftedMessageIds((prev) => new Set(prev).add(proposal.proposal_id));
      toast.success(`Draft purchase order ${confirmed.ref} confirmed.`, {
        description: `${proposal.quantity} ${proposal.unit || "units"} for facility #${proposal.facility_id}.`,
        action: { label: "Review Orders", onClick: () => router.push("/orders") },
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not confirm draft order proposal.");
    }
  }

  async function ensureConversation(): Promise<string> {
    if (conversationId) return conversationId;
    const created = (await apiFetch("copilot", "/conversations", {
      method: "POST",
      body: JSON.stringify({ facility_id: facility.id }),
    })) as { id: string };
    setConversationId(created.id);
    return created.id;
  }

  async function streamReply(userText: string) {
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;

    const replyId = id();
    setMessages((m) => [...m, { id: replyId, role: "assistant", text: "", live: true, tools: [] }]);

    const applyToReply = (fn: (msg: Message) => Message) =>
      setMessages((m) => m.map((msg) => (msg.id === replyId ? fn(msg) : msg)));

    try {
      const cid = await ensureConversation();
      const focusPayload =
        focus?.kind === "sku"
          ? { type: "sku", ndc: focus.ndc ?? focus.itemId, facility_id: facility.id }
          : focus?.kind === "alert" && focus.ndc
            ? { type: "alert", ndc: focus.ndc, facility_id: facility.id }
            : focus?.kind === "patient"
              ? { type: "patient", patient_id: focus.patientId, rxcui: focus.rxcui, drug_name: focus.drugName, facility_id: facility.id }
              : null;
      const ndcTag = focus?.kind === "sku" ? (focus.ndc ? ` (NDC ${focus.ndc})` : "") : "";
      const patientTag = focus?.kind === "patient" ? ` [Patient ID: ${focus.patientId}]` : "";
      const contextPrefix = focus ? `[Currently viewing: ${focus.label}${ndcTag}${patientTag} — ${focus.detail}]\n\n` : "";
      for await (const evt of streamCopilotMessage(
        { conversation_id: cid, text: contextPrefix + userText, focus: focusPayload },
        controller.signal,
      )) {
        if (evt.event === "delta") {
          applyToReply((msg) => ({ ...msg, text: msg.text + evt.data.text }));
        } else if (evt.event === "tool_start") {
          applyToReply((msg) => ({
            ...msg,
            tools: [...(msg.tools ?? []), { name: evt.data.name, status: "running" }],
          }));
        } else if (evt.event === "tool_end") {
          applyToReply((msg) => ({
            ...msg,
            tools: (msg.tools ?? []).map((t) =>
              t.name === evt.data.name && t.status === "running"
                ? { name: t.name, status: evt.data.ok ? "done" : "error", error: evt.data.error }
                : t,
            ),
          }));
        } else if (evt.event === "tool_card") {
          const { card } = evt.data;
          applyToReply((msg) => ({
            ...msg,
            cards: [...(msg.cards ?? (msg.card ? [msg.card] : [])), card as ResponseCard],
            card: card as ResponseCard,
          }));
        } else if (evt.event === "degraded") {
          applyToReply((msg) => ({ ...msg, text: evt.data.reason, degraded: true }));
        } else if (evt.event === "patient_disambiguation") {
          const { query, candidates } = evt.data;
          applyToReply((msg) => ({
            ...msg,
            text:
              msg.text ||
              `I found ${candidates.length} patients matching "${query}" — which one did you mean?`,
            patientPicker: { query, candidates },
          }));
        }
      }
      void refreshHistory();
    } catch {
      if (controller.signal.aborted) return;
      applyToReply((msg) => ({
        ...msg,
        text: msg.text || "Something went wrong reaching the AI assistant. Try again in a moment.",
        degraded: true,
      }));
    } finally {
      if (streamAbortRef.current === controller) {
        streamAbortRef.current = null;
        setPending(false);
      }
    }
  }

  function pickPatient(messageId: string, candidate: PatientCandidate) {
    if (pending) return;
    const priorMessages = messages.map((msg) =>
      msg.id === messageId ? { ...msg, patientPicker: undefined } : msg,
    );
    const text = `Use patient_id ${candidate.id} for my previous request.`;
    setMessages([...priorMessages, { id: id(), role: "user", text }]);
    setPending(true);
    void streamReply(text);
  }

  function send() {
    if (pending) return;
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setMessages((m) => [...m, { id: id(), role: "user", text }]);
    setPending(true);
    void streamReply(text);
  }

  if (!open) {
    return (
      <div className="hidden shrink-0 border-l bg-card lg:flex lg:w-12 lg:flex-col lg:items-center lg:gap-1 lg:py-3">
        <Button variant="ghost" size="icon" onClick={() => setOpen(true)} aria-label="Open AI MedStock Assistant">
          <Bot />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => {
            setOpen(true);
            setHistoryOpen(true);
          }}
          aria-label="Open conversation history"
        >
          <History />
        </Button>
      </div>
    );
  }

  const panel = (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Sparkles className="size-4 text-primary" />
          <span className="text-sm font-semibold">AI MedStock Assistant</span>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" onClick={() => setHistoryOpen(true)} aria-label="Conversation history">
            <History />
          </Button>
          <Button variant="ghost" size="icon" onClick={clearConversation} aria-label="Clear conversation" disabled={messages.length <= 1}>
            <Eraser />
          </Button>
          <Button variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="Collapse AI MedStock Assistant">
            <X />
          </Button>
        </div>
      </div>

      {focus && (
        <div className="border-b bg-muted/40 px-3 py-2 text-xs">
          <span className="text-muted-foreground">Context: </span>
          <span className="font-medium">{focus.label}</span>
          <p className="mt-0.5 truncate text-muted-foreground">{focus.detail}</p>
        </div>
      )}

      <div className="flex flex-wrap gap-1.5 border-b px-3 py-2">
        {quickActions.map(({ key, label, icon: Icon }) => (
          <Button key={key} variant="outline" size="sm" className="h-7 text-xs" onClick={() => runAction(key)}>
            <Icon data-icon="inline-start" />
            {label}
          </Button>
        ))}
      </div>

      <ScrollArea className="min-h-0 flex-1 px-3 py-3">
        <div className="flex flex-col gap-3" role="log" aria-live="polite" aria-relevant="additions" aria-busy={pending}>
          {messages.map((m) => {
            const allCards = m.cards?.length ? m.cards : m.card ? [m.card] : [];
            return (
              <div key={m.id} className={cn("flex flex-col gap-1.5", m.role === "user" && "items-end")}>
                {m.tools && <ToolActivityRow tools={m.tools} />}
                {allCards.map((c, i) => {
                  const cardKey = c.kind === "proposal" ? c.proposal_id : `${m.id}-${i}`;
                  return (
                    <div key={cardKey} className="flex w-[92%] flex-col gap-1">
                      <ResponseCardView
                        card={c}
                        onCreateDraft={c.kind === "po" ? () => createDraftFromCard(cardKey, c as Extract<ResponseCard, { kind: "po" }>) : undefined}
                        onConfirmProposal={c.kind === "proposal" ? () => confirmProposal(c as Extract<ResponseCard, { kind: "proposal" }>) : undefined}
                        onAskFollowup={(prompt) => {
                          setMessages((prev) => [...prev, { id: id(), role: "user", text: prompt }]);
                          setPending(true);
                          void streamReply(prompt);
                        }}
                        drafted={draftedMessageIds.has(cardKey)}
                      />
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 w-fit gap-1.5 self-end text-[11px] text-muted-foreground"
                      onClick={() => {
                        navigator.clipboard.writeText(formatCardForCopy(c));
                        toast.success("Copied to clipboard.");
                      }}
                    >
                      <Copy className="size-3" />
                      Copy
                    </Button>
                    </div>
                  );
                })}
                {m.text && (
                  <div
                    className={cn(
                      "max-w-[92%] rounded-lg px-3 py-2 text-sm",
                      m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted",
                      m.degraded && "border border-amber-500/40",
                    )}
                  >
                    {m.role === "assistant" ? (
                      m.degraded ? (
                        <p className="flex items-start gap-1.5 text-sm leading-relaxed text-muted-foreground">
                          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-500" />
                          {m.text}
                        </p>
                      ) : m.live ? (
                        <LiveText text={m.text} />
                      ) : (
                        <StreamingText text={m.text} />
                      )
                    ) : (
                      m.text
                    )}
                  </div>
                )}
                {m.patientPicker && (
                  <div className="flex w-[92%] flex-col gap-1">
                    {m.patientPicker.candidates.map((c) => (
                      <Button
                        key={c.id}
                        variant="outline"
                        size="sm"
                        className="h-auto justify-between gap-2 px-2.5 py-1.5 text-left text-xs"
                        onClick={() => pickPatient(m.id, c)}
                      >
                        <span className="font-medium">{c.full_name}</span>
                        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                          DOB {c.date_of_birth} · {c.id.slice(0, 8)}
                        </span>
                      </Button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      <Separator />
      <div className="flex flex-col gap-1 p-3">
        <div className="flex items-end gap-2">
          <Textarea
            ref={textareaRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onInput={(e) => {
              const el = e.currentTarget;
              el.style.height = "auto";
              el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Ask the assistant…"
            className="min-h-9 resize-none py-2 text-sm"
            rows={1}
            disabled={pending}
          />
          <Button size="icon" onClick={send} aria-label="Send message" disabled={pending}>
            <Send />
          </Button>
        </div>
        <span className="text-[10px] text-muted-foreground">Enter to send · Shift+Enter for a new line</span>
      </div>
    </div>
  );

  const historyDialog = (
    <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Conversation history</DialogTitle>
          <DialogDescription>Past AI MedStock Assistant conversations at {facility.name}.</DialogDescription>
        </DialogHeader>
        <Button variant="outline" size="sm" className="w-fit gap-1.5 text-xs" onClick={() => void startNewConversation()}>
          <Plus data-icon="inline-start" />
          New chat
        </Button>
        {history.length === 0 ? (
          <p className="py-6 text-center text-xs text-muted-foreground">No past conversations yet.</p>
        ) : (
          <div className="flex flex-col gap-1">
            {history.map((h) => (
              <button
                key={h.id}
                onClick={() => void openConversation(h)}
                className="flex flex-col gap-0.5 rounded-md border px-3 py-2 text-left text-xs hover:bg-muted"
              >
                <span className="truncate font-medium">{h.title || "New conversation"}</span>
                <span className="text-[11px] text-muted-foreground">
                  {h.savedAt
                    ? new Date(h.savedAt).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
                    : ""}
                </span>
              </button>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );

  if (isDesktop) {
    return (
      <>
        <aside className="flex min-h-0 w-[380px] shrink-0 flex-col border-l bg-card">{panel}</aside>
        {historyDialog}
      </>
    );
  }

  return (
    <Sheet open onOpenChange={(next) => !next && setOpen(false)}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-md">
        <SheetTitle className="sr-only">AI MedStock Assistant</SheetTitle>
        {panel}
      </SheetContent>
      {historyDialog}
    </Sheet>
  );
}
