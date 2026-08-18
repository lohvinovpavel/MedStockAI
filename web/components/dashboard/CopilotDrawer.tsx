"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { AlertTriangle, Bot, CheckCircle2, Copy, Eraser, FileText, History, Loader2, Plane, Plus, Repeat2, ShieldCheck, Send, Truck, X, Sparkles } from "lucide-react";
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
import { useMediaQuery } from "@/lib/use-media-query";
import { forecastFor, inventoryFor, isoPlusDays, parLevel, suppliers } from "@/lib/mock-data";
import { apiFetch, streamCopilotChat, type CopilotMessage } from "@/lib/api";
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
      drugName: string;
      supplier: string;
      quantity: number;
      unit: string;
      unitCost: number;
      totalCost: number;
      coverageDays: number;
      leadTimeDays: number;
      confidence: number;
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
};

let nextId = 1;
function id() {
  return `m-${nextId++}`;
}

// Past conversations, persisted so "history" survives a refresh — same
// localStorage pattern as the open/collapsed flag in copilot-context.tsx.
type SavedConversation = { id: string; savedAt: number; messages: Message[] };
const HISTORY_STORAGE_KEY = "medstock-copilot-history";
const GREETING: Message = {
  id: "m-greeting",
  role: "assistant",
  text: "Hi, I'm the AI MedStock Assistant. Select a SKU or alert on the page, or ask me anything about inventory, forecasts, and shortages.",
};

const QUICK_ACTIONS = [
  { key: "po", label: "Generate PO", icon: FileText },
  { key: "analogue", label: "Find Bio-Equivalent", icon: Repeat2 },
  { key: "certificate", label: "Check Certificate", icon: ShieldCheck },
] as const;

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

// Reads the same mock-data every other page reads, keyed off whatever SKU
// is actually focused — the quick actions used to return fixed literals
// (a hardcoded PO regardless of drug, analogues from facilities that no
// longer exist) with no connection to what was on screen.
async function replyFor(action: string, focus: CopilotFocus, facilityId: string): Promise<Message> {
  if (focus?.kind !== "sku") {
    return {
      id: id(),
      role: "assistant",
      text: "Select a SKU on Inventory, Forecasts, or Audit first — I need to know which drug you mean before I can answer.",
    };
  }

  const item = inventoryFor(facilityId).find((i) => i.id === focus.itemId);
  if (!item) {
    return { id: id(), role: "assistant", text: `${focus.label} isn't stocked at the active facility.` };
  }

  if (action === "po") {
    const forecast = forecastFor(facilityId, item.id);
    if (!forecast) {
      return {
        id: id(),
        role: "assistant",
        text: `${item.drugName} has no trained forecast model, so I can't draft a data-backed purchase order for it.`,
      };
    }
    const { supplier, unit, unitCost, leadTimeDays } = forecast.purchaseOrder;
    // Order enough to reach a 30-day par level at the model's own predicted
    // rate, same derivation as the Forecasts page — not a stored literal.
    const points = forecast.series.filter((p) => p.forecast != null).map((p) => p.forecast!);
    const avgDailyForecast = points.length > 0 ? points.reduce((sum, v) => sum + v, 0) / points.length : 0;
    const coverageDays = 30;
    const quantity = Math.max(1, parLevel(avgDailyForecast, coverageDays) - item.currentStock);
    return {
      id: id(),
      role: "assistant",
      text: `Drafted a purchase order for ${item.drugName} based on the current burn rate and a ${coverageDays}-day coverage target.`,
      card: {
        kind: "po",
        itemId: item.id,
        drugName: item.drugName,
        supplier,
        quantity,
        unit,
        unitCost,
        totalCost: quantity * unitCost,
        coverageDays,
        leadTimeDays,
        confidence: forecast.confidence,
      },
    };
  }
  if (action === "analogue") {
    const ranked = [...item.analogues].sort((a, b) => b.matchScore - a.matchScore);
    if (ranked.length === 0) {
      return { id: id(), role: "assistant", text: `No RxNorm or ATC equivalents are registered for ${item.drugName}.` };
    }
    return {
      id: id(),
      role: "assistant",
      text: `Found ${ranked.length} bio-equivalent analogue${ranked.length === 1 ? "" : "s"} for ${item.drugName}, best match first.`,
      card: {
        kind: "analogues",
        items: ranked.slice(0, 3).map((a) => ({
          name: a.drugName,
          matchScore: a.matchScore,
          stockHere: a.stockByFacility[facilityId] ?? 0,
        })),
      },
    };
  }
  if (action === "certificate") {
    // Real compliance data, same source as the shelf badge. Fetched here rather
    // than in the card view so the message keeps working the way every other
    // card does: it carries its data, which is what makes copy-to-clipboard
    // able to quote a status it can actually see.
    //
    // A chat message is a snapshot by nature -- it records what was true when
    // asked, and is not expected to repaint later.
    const result = await certificateStatus(item.ndc);
    return {
      id: id(),
      role: "assistant",
      text: certificateText(item.drugName, result.status),
      card: {
        kind: "certificate",
        ndc: item.ndc,
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
    text: `I can help with ${item.drugName}. Ask about stock coverage, bio-equivalents, restock timing, or certificate status.`,
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
  const { open, setOpen, focus, emergencyRequest } = useCopilot();
  const { facilityId, facility } = useFacility();
  const { addOrder } = useOrders();
  // Below `lg` the panel opens as a Sheet instead of a flex sibling of
  // `main` (there's no room for a fixed 380px column at phone/tablet
  // widths). Branching on a real viewport check — rather than mounting
  // both an inline aside and a Sheet and CSS-hiding one — matters here
  // because this component owns live conversation state; two mounted
  // instances would silently diverge.
  const isDesktop = useMediaQuery("(min-width: 1024px)");
  const [messages, setMessages] = useState<Message[]>([GREETING]);
  const [draft, setDraft] = useState("");
  const [history, setHistory] = useState<SavedConversation[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  useEffect(() => {
    const stored = window.localStorage.getItem(HISTORY_STORAGE_KEY);
    if (stored) setHistory(JSON.parse(stored));
  }, []);
  // Message ids whose PO card has already been turned into a real draft
  // order — keyed by message, not by drug, so two suggestions for the same
  // SKU in one conversation stay independent.
  const [draftedMessageIds, setDraftedMessageIds] = useState<Set<string>>(new Set());
  // Drives aria-busy on the message log while a reply is in flight — the
  // 300ms canned-reply delay is otherwise imperceptible to assistive tech.
  const [pending, setPending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const lastHandledNonce = useRef<number | null>(null);
  // The in-flight /copilot/chat stream, if any — a new message aborts the
  // previous one rather than letting two streams write into the same
  // conversation, and unmounting the drawer aborts whatever's still open.
  const streamAbortRef = useRef<AbortController | null>(null);
  useEffect(() => () => streamAbortRef.current?.abort(), []);

  // A page (e.g. the forecast scenario simulator) can fire a one-shot
  // "emergency plan" ask via the copilot context — post it as a user
  // message and stream back the structured plan, same as a quick action.
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

  function runAction(action: string) {
    if (focus) {
      setMessages((m) => [...m, { id: id(), role: "user", text: `${QUICK_ACTIONS.find((a) => a.key === action)?.label} — ${focus.label}` }]);
    } else {
      setMessages((m) => [...m, { id: id(), role: "user", text: QUICK_ACTIONS.find((a) => a.key === action)?.label ?? action }]);
    }
    setPending(true);
    window.setTimeout(async () => {
      const reply = await replyFor(action, focus, facilityId);
      setMessages((m) => [...m, reply]);
      setPending(false);
    }, 300);
  }

  function clearConversation() {
    setMessages([GREETING]);
  }

  // Archives the active conversation (if anything actually happened beyond
  // the canned greeting) then persists it — this backs both "start a new
  // chat" and "switch to a past one", so neither one silently drops what
  // was on screen.
  function archiveCurrent(current: Message[]) {
    if (current.length <= 1) return;
    setHistory((prev) => {
      const next = [{ id: id(), savedAt: Date.now(), messages: current }, ...prev];
      window.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }

  function startNewConversation() {
    archiveCurrent(messages);
    setMessages([GREETING]);
    setHistoryOpen(false);
  }

  function openConversation(saved: SavedConversation) {
    archiveCurrent(messages);
    setMessages(saved.messages);
    setHistoryOpen(false);
  }

  // Same order pipeline the Forecasts page suggestion writes to — lands in
  // /orders as a draft awaiting review, not a dispatch. Previously
  // "Generate PO" here produced a card and nothing else, while the
  // identically-named action on Forecasts created a real order.
  function createDraftFromCard(messageId: string, card: Extract<ResponseCard, { kind: "po" }>) {
    const supplier = suppliers.find((s) => s.name === card.supplier) ?? suppliers[0];
    const order = addOrder({
      facilityId,
      supplierId: supplier.id,
      drugId: card.itemId,
      drugName: card.drugName,
      quantity: card.quantity,
      unit: card.unit,
      unitCost: card.unitCost,
      shipping: supplier.shippingFlat,
      status: "draft",
      source: "ai_suggestion",
      expectedDelivery: isoPlusDays(card.leadTimeDays),
      note: `Generated from ${card.confidence}% confidence forecast via AI MedStock Assistant.`,
    });
    setDraftedMessageIds((prev) => new Set(prev).add(messageId));
    toast.success(`Draft order ${order.id} created.`, {
      description: `${card.quantity} ${card.unit} of ${card.drugName} for ${facility.name}.`,
      action: { label: "Review", onClick: () => router.push("/orders") },
    });
  }

  // Real turns only — the greeting is UI chrome, not something the assistant
  // actually said, and sending it back as a prior "model" turn would have
  // Gemini responding to a message it never produced. `role: "model"` (not
  // "assistant") is Gemini's own vocabulary — services/analogue/app/copilot.py
  // passes it straight through to `types.Content(role=...)`.
  function toCopilotHistory(history: Message[]): CopilotMessage[] {
    return history
      .filter((m) => m.id !== GREETING.id)
      .map((m) => ({ role: m.role === "assistant" ? ("model" as const) : ("user" as const), text: m.text }));
  }

  async function streamReply(userText: string, priorMessages: Message[]) {
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;

    const replyId = id();
    setMessages((m) => [...m, { id: replyId, role: "assistant", text: "", live: true, tools: [] }]);

    const applyToReply = (fn: (msg: Message) => Message) =>
      setMessages((m) => m.map((msg) => (msg.id === replyId ? fn(msg) : msg)));

    try {
      const history = [...toCopilotHistory(priorMessages), { role: "user" as const, text: userText }];
      for await (const evt of streamCopilotChat(history, controller.signal)) {
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
        }
        // "done" carries only a request_id — nothing left to apply to the message.
      }
    } catch {
      if (controller.signal.aborted) return; // superseded by a newer message, not a failure
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

  function send() {
    if (pending) return; // one stream at a time — the real turn can take seconds, not the old 300ms mock delay
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    // The textarea auto-grows with content; reset it back to one line once
    // the message is sent rather than leaving it at whatever height the
    // last message left it.
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    const priorMessages = messages;
    setMessages((m) => [...m, { id: id(), role: "user", text }]);
    setPending(true);
    void streamReply(text, priorMessages);
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
        {QUICK_ACTIONS.map(({ key, label, icon: Icon }) => (
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
        <Button variant="outline" size="sm" className="w-fit gap-1.5 text-xs" onClick={startNewConversation}>
          <Plus data-icon="inline-start" />
          New chat
        </Button>
        {history.length === 0 ? (
          <p className="py-6 text-center text-xs text-muted-foreground">No past conversations yet.</p>
        ) : (
          <div className="flex flex-col gap-1">
            {history.map((h) => {
              const preview = h.messages.find((m) => m.role === "user")?.text ?? "New conversation";
              return (
                <button
                  key={h.id}
                  onClick={() => openConversation(h)}
                  className="flex flex-col gap-0.5 rounded-md border px-3 py-2 text-left text-xs hover:bg-muted"
                >
                  <span className="truncate font-medium">{preview}</span>
                  <span className="text-[11px] text-muted-foreground">
                    {new Date(h.savedAt).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })} · {h.messages.length} messages
                  </span>
                </button>
              );
            })}
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
