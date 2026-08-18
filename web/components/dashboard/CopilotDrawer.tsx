"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { AlertTriangle, Bot, CheckCircle2, Copy, Eraser, FileText, History, Loader2, Plane, Plus, Repeat2, ShieldCheck, Send, Siren, Truck, X, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { StatusBadge } from "@/components/dashboard/StatusBadge";
import { useCopilot, type CopilotFocus, type EmergencyPlanRequest } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { useOrders } from "@/lib/orders-context";
import { useSession } from "@/lib/session";
import { useMediaQuery } from "@/lib/use-media-query";
import { apiFetch, streamCopilotMessage, type PatientCandidate } from "@/lib/api";
import {
  CERT_LABELS,
  CERT_TONE,
  type CertResult,
  type CertStatus,
} from "@/components/CertificationBadge";
import { cn } from "@/lib/utils";

type ResponseCard =
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
  | { kind: "analogues"; items: { name: string; matchScore: number; stockHere: number }[] }
  | {
      kind: "certificate";
      ndc: string;
      status: CertStatus;
      reasons: number;
      transient: number;
      persistent: number;
    }
  | { kind: "emergency"; drugName: string; surgePct: number; depletionDays: number | null; airFreightDays: number; costPremiumPct: number };

// One tool the real copilot called mid-turn (services/analogue/app/copilot.py's
// tool_start/tool_end events) — rendered as a small pill so a pharmacist can
// see what the assistant actually looked up, not just the answer it gives.
type ToolActivity = { name: string; status: "running" | "done" | "error"; error?: string };

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  card?: ResponseCard;
  // True only for a message built from the real `/copilot/chat` stream — its
  // text arrives incrementally already, so it skips StreamingText's fake
  // character-reveal (built for a string that's complete the moment it
  // renders) and is drawn as-is instead.
  live?: boolean;
  // A `degraded` event replaced this message's text with the fallback
  // explanation rather than a real answer — styled differently so it doesn't
  // read as if the assistant found nothing to say.
  degraded?: boolean;
  tools?: ToolActivity[];
  // A name the user typed matched more than one patient — rendered as a
  // picker (name, DOB, ID) instead of/alongside the message text. Cleared
  // once a candidate is picked so the card doesn't linger as a dead control.
  patientPicker?: { query: string; candidates: PatientCandidate[] };
};

let nextId = 1;
function id() {
  return `m-${nextId++}`;
}

// Past conversations, persisted by I2 (`GET /api/copilot/conversations`).
type SavedConversation = { id: string; savedAt: number; title?: string | null; messages: Message[] };
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

// PH-1 (docs/ai_workflows.md): one question chains the three real copilot
// tools (check_stock_by_ndc, search_analogues_rxnorm, verify_batch_cert) in
// one turn instead of four screens.
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

/**
 * One NDC's traffic light, from the same `GET /status` the shelf uses.
 *
 * An unreachable service resolves to `unavailable`, never to `green`. Telling a
 * pharmacist a drug is certified because the check failed is the one answer
 * this feature exists to prevent, and a chat card asserts it more plainly than
 * a badge does.
 */
async function certificateStatus(ndc: string): Promise<CertResult> {
  const unavailable: CertResult = { status: "unavailable", reasons: 0, transient: 0, persistent: 0 };
  if (!ndc) return unavailable;
  try {
    const body = await apiFetch("compliance", `/status?ndc=${encodeURIComponent(ndc)}`);
    const row = (body?.results ?? [])[0] as CertResult | undefined;
    return row ?? unavailable;
  } catch {
    return unavailable;
  }
}

function certificateText(drugName: string, status: CertStatus): string {
  if (status === "unavailable") {
    return `I could not reach the compliance service, so ${drugName} has not been checked. This is not a clean result — treat it as unknown.`;
  }
  if (status === "unknown") {
    return `No FDA certification record is held for ${drugName}. That is not the same as clean: nothing has been checked against it.`;
  }
  if (status === "green") {
    return `${drugName} is actively marketed with no open recall.`;
  }
  return `${drugName} has open findings against it — details below.`;
}

// Live F1 / analogue / compliance reads — no model required (I1 rule 5).
async function replyFor(
  action: string,
  focus: CopilotFocus,
  facilityPk: number,
): Promise<Message> {
  if (focus?.kind !== "sku") {
    return {
      id: id(),
      role: "assistant",
      text: "Select a SKU on Inventory, Forecasts, or Audit first — I need to know which drug you mean before I can answer.",
    };
  }

  const ndc = focus.ndc || focus.itemId;
  let rxcui = focus.rxcui ?? null;
  let onHand: number | null = null;
  try {
    const body = (await apiFetch(
      "inventory",
      `/items?facility_id=${facilityPk}&limit=200`,
    )) as { items: { ndc: string; name: string | null; quantity: number; rxcui?: string | null }[] };
    const row = (body.items ?? []).find((i) => i.ndc === ndc);
    if (!row) {
      return { id: id(), role: "assistant", text: `${focus.label} isn't stocked at the active facility.` };
    }
    onHand = row.quantity;
    rxcui = rxcui || row.rxcui || null;
  } catch {
    return { id: id(), role: "assistant", text: `I could not reach inventory for ${focus.label}.` };
  }

  if (action === "po") {
    try {
      const body = (await apiFetch(
        "prediction",
        `/recommendations?facility_id=${facilityPk}&ndc=${encodeURIComponent(ndc)}`,
      )) as { items: Record<string, unknown>[] };
      const rec = body.items?.[0];
      if (!rec) {
        return {
          id: id(),
          role: "assistant",
          text: `${focus.label} has no restock recommendation — either it is already at par or no supplier lists this NDC.`,
        };
      }
      const quantity = Number(rec.quantity) || 0;
      const unitCost = Number(rec.unit_cost) || 0;
      return {
        id: id(),
        role: "assistant",
        text: `Drafted a purchase order for ${rec.name ?? focus.label} from live par, on-hand (${onHand ?? "—"}), and the supplier catalog.`,
        card: {
          kind: "po",
          itemId: ndc,
          ndc,
          drugName: String(rec.name ?? focus.label),
          supplier: String(rec.supplier_name ?? ""),
          quantity,
          unit: String(rec.unit ?? "units"),
          unitCost,
          totalCost: Number(rec.estimated_total) || quantity * unitCost,
          coverageDays: Number(rec.coverage_days) || 30,
          leadTimeDays: Number(rec.lead_time_days) || 0,
          confidence: 0,
          payload: rec,
        },
      };
    } catch {
      return { id: id(), role: "assistant", text: `I could not load a restock recommendation for ${focus.label}.` };
    }
  }
  if (action === "analogue") {
    if (!rxcui) {
      return { id: id(), role: "assistant", text: `${focus.label} has no RxCUI on file, so I cannot look up bio-equivalents.` };
    }
    try {
      const body = (await apiFetch(
        "analogue",
        `/analogues/${encodeURIComponent(rxcui)}?facility_id=${facilityPk}&use_ai=false`,
      )) as {
        items: {
          name: string;
          quantity?: number;
          availability?: { quantity?: number };
        }[];
      };
      const ranked = body.items ?? [];
      if (ranked.length === 0) {
        return { id: id(), role: "assistant", text: `No RxNorm equivalents are registered for ${focus.label}.` };
      }
      return {
        id: id(),
        role: "assistant",
        text: `Found ${ranked.length} bio-equivalent analogue${ranked.length === 1 ? "" : "s"} for ${focus.label}, best stocked first.`,
        card: {
          kind: "analogues",
          items: ranked.slice(0, 3).map((a, i) => ({
            name: a.name,
            matchScore: Math.max(10, 100 - i * 12),
            stockHere: a.availability?.quantity ?? a.quantity ?? 0,
          })),
        },
      };
    } catch {
      return { id: id(), role: "assistant", text: `I could not reach analogue search for ${focus.label}.` };
    }
  }
  if (action === "certificate") {
    const result = await certificateStatus(ndc);
    return {
      id: id(),
      role: "assistant",
      text: certificateText(focus.label, result.status),
      card: {
        kind: "certificate",
        ndc,
        status: result.status,
        reasons: result.reasons ?? 0,
        transient: result.transient ?? 0,
        persistent: result.persistent ?? 0,
      },
    };
  }
  return {
    id: id(),
    role: "assistant",
    text: `I can help with ${focus.label}. Ask about stock coverage, bio-equivalents, restock timing, or certificate status.`,
  };
}

function emergencyPlanReply(req: EmergencyPlanRequest): Message {
  const costPremiumPct = req.surgePct >= 200 ? 45 : req.surgePct >= 150 ? 25 : 10;
  return {
    id: id(),
    role: "assistant",
    text:
      req.depletionDays != null
        ? `Emergency supply plan for ${req.drugName} at ${req.surgePct}% projected load — current stock depletes in ~${req.depletionDays} day${req.depletionDays === 1 ? "" : "s"}. Recommending expedited air freight to close the gap.`
        : `Emergency supply plan for ${req.drugName} at ${req.surgePct}% projected load — stock holds beyond the 30-day forecast window at this rate.`,
    card: { kind: "emergency", drugName: req.drugName, surgePct: req.surgePct, depletionDays: req.depletionDays, airFreightDays: 2, costPremiumPct },
  };
}

// Real deltas from `/copilot/chat` already arrive incrementally — replaying
// them through StreamingText's fake reveal would restart that animation on
// every chunk. Rendered plain, growing as the stream does; the sr-only/
// aria-hidden split isn't needed here for the same reason it exists on
// StreamingText: a live region wouldn't announce a fake reveal it never sees.
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
      {/* The animated reveal is presentational only — a live region
          watching it would announce every partial word as it grows. The
          full text is present from the same render as a hidden sibling, so
          the one DOM insertion assistive tech sees is the complete message. */}
      <p className="text-sm leading-relaxed" aria-hidden="true">{shown}</p>
      <p className="sr-only">{text}</p>
    </>
  );
}

// Plain-text summary for the clipboard — a purchase order or emergency
// plan is something a user plausibly wants to paste into an email or a
// ticket, not just look at.
function formatCardForCopy(card: ResponseCard): string {
  if (card.kind === "po") {
    return `Draft Purchase Order — ${card.drugName}\nSupplier: ${card.supplier}\nQuantity: ${card.quantity} ${card.unit}\nCoverage: ${card.coverageDays} days\nEst. total: $${card.totalCost.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  }
  if (card.kind === "analogues") {
    return ["Bio-Equivalent Analogues", ...card.items.map((it) => `${it.name} — ${it.matchScore}% match — ${it.stockHere > 0 ? `${it.stockHere} in stock` : "not stocked here"}`)].join("\n");
  }
  if (card.kind === "certificate") {
    const lines = [
      "Certificate Status",
      `NDC: ${card.ndc}`,
      `Status: ${CERT_LABELS[card.status]}`,
    ];
    if (card.reasons > 0) {
      // Standing vs transient is the part worth pasting into a ticket: a recall
      // clears, a dead listing does not.
      lines.push(`Findings: ${card.reasons} (${card.persistent} standing, ${card.transient} transient)`);
    }
    return lines.join("\n");
  }
  return `Emergency Supply Plan\nScenario: ${card.surgePct}% of baseline demand\nStock depletes in: ${card.depletionDays != null ? `${card.depletionDays}d` : "30d+"}\nFreight: Air (expedited), ${card.airFreightDays} days\nCost premium: +${card.costPremiumPct}%`;
}

function ResponseCardView({
  card,
  onCreateDraft,
  drafted,
}: {
  card: ResponseCard;
  onCreateDraft?: () => void;
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
  if (card.kind === "analogues") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3">
          <CardTitle className="text-xs font-medium text-muted-foreground">Bio-Equivalent Analogues</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 px-3 text-sm">
          {card.items.map((it) => (
            <div key={it.name} className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate">{it.name}</p>
                <p className="truncate text-xs text-muted-foreground">{it.matchScore}% match</p>
              </div>
              <Badge variant={it.stockHere > 0 ? "secondary" : "outline"}>
                {it.stockHere > 0 ? `${it.stockHere} in stock` : "Not stocked here"}
              </Badge>
            </div>
          ))}
        </CardContent>
      </Card>
    );
  }
  if (card.kind === "certificate") {
    return (
      <Card className="gap-2 py-3">
        <CardHeader className="px-3">
          <CardTitle className="text-xs font-medium text-muted-foreground">Certificate Status</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-1 px-3 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Status</span>
            <StatusBadge tone={CERT_TONE[card.status]} className="normal-case">
              {CERT_LABELS[card.status]}
            </StatusBadge>
          </div>
          <div className="flex justify-between"><span className="text-muted-foreground">NDC</span><span className="font-mono text-xs">{card.ndc}</span></div>
          {card.reasons > 0 && (
            <div className="flex justify-between">
              <span className="text-muted-foreground">Findings</span>
              <span>{card.persistent} standing · {card.transient} transient</span>
            </div>
          )}
        </CardContent>
      </Card>
    );
  }
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

  function mapApiMessages(items: { id: number; role: string; text: string | null; card?: ResponseCard | null }[]): Message[] {
    const mapped = items
      .filter((row) => row.role === "user" || row.role === "assistant")
      .map((row) => ({
        id: `api-${row.id}`,
        role: row.role as "user" | "assistant",
        text: row.text ?? "",
        card: row.card ?? undefined,
        live: true,
      }));
    return mapped.length === 0 ? [GREETING] : mapped;
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
          items: { id: number; role: string; text: string | null; card?: ResponseCard | null }[];
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
    setMessages((m) => [...m, { id: id(), role: "user", text: `Generate emergency supply plan for current load — ${emergencyRequest.drugName}` }]);
    setPending(true);
    window.setTimeout(() => {
      setMessages((m) => [...m, emergencyPlanReply(emergencyRequest)]);
      setPending(false);
    }, 300);
  }, [emergencyRequest]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  function runAction(actionKey: string) {
    if (pending) return; // one stream at a time, same rule send() follows
    const action = quickActions.find((a) => a.key === actionKey);
    const label = action?.label ?? actionKey;
    setMessages((m) => [...m, { id: id(), role: "user", text: focus ? `${label} — ${focus.label}` : label }]);
    setPending(true);
    if (action?.prompt) {
      void streamReply(action.prompt);
      return;
    }
    window.setTimeout(async () => {
      const reply = await replyFor(actionKey, focus, facility.id);
      setMessages((m) => [...m, reply]);
      setPending(false);
    }, 300);
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
        items: { id: number; role: string; text: string | null; card?: ResponseCard | null }[];
      };
      setConversationId(conv.id);
      setMessages(mapApiMessages(conv.items ?? []));
    } catch {
      toast.error("Could not load that conversation.");
    }
    setHistoryOpen(false);
  }

  async function createDraftFromCard(messageId: string, card: Extract<ResponseCard, { kind: "po" }>) {
    try {
      const rec = (await apiFetch("inventory", "/recommendations", {
        method: "POST",
        body: JSON.stringify({ facility_id: facility.id, payload: card.payload }),
      })) as { id: number };
      const order = (await apiFetch("inventory", `/recommendations/${rec.id}/approve`, {
        method: "POST",
      })) as { ref: string };
      reloadOrders();
      setDraftedMessageIds((prev) => new Set(prev).add(messageId));
      toast.success(`Draft order ${order.ref} created.`, {
        description: `${card.quantity} ${card.unit} of ${card.drugName} for ${facility.name}.`,
        action: { label: "Review", onClick: () => router.push("/orders") },
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not create draft order.");
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

  // The candidate list came straight from the backend's disambiguation event,
  // never through Gemini -- picking one must keep it that way. The resend
  // carries only the UUID, exactly what a physician would have pasted
  // directly before this feature existed, so the model can retry the same
  // tool call already in its history with a resolved id instead of a name.
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
          {messages.map((m) => (
            <div key={m.id} className={cn("flex flex-col gap-1.5", m.role === "user" && "items-end")}>
              {m.tools && <ToolActivityRow tools={m.tools} />}
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
              {m.card && (
                <div className="flex w-[92%] flex-col gap-1">
                  <ResponseCardView
                    card={m.card}
                    onCreateDraft={m.card.kind === "po" ? () => createDraftFromCard(m.id, m.card as Extract<ResponseCard, { kind: "po" }>) : undefined}
                    drafted={draftedMessageIds.has(m.id)}
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-fit gap-1.5 self-end text-[11px] text-muted-foreground"
                    onClick={() => {
                      navigator.clipboard.writeText(formatCardForCopy(m.card!));
                      toast.success("Copied to clipboard.");
                    }}
                  >
                    <Copy className="size-3" />
                    Copy
                  </Button>
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
          ))}
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
              // Auto-grow up to ~6 lines, then let the textarea's own
              // scrollbar take over — previously fixed at one line, so
              // anything longer than ~40 characters scrolled inside a
              // sliver the user couldn't read before sending.
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
