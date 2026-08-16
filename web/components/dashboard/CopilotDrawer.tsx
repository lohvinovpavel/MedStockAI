"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { AlertTriangle, Bot, CheckCircle2, Copy, Eraser, FileText, Plane, Repeat2, ShieldCheck, Send, Truck, X, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { StatusBadge } from "@/components/dashboard/StatusBadge";
import { useCopilot, type CopilotFocus, type EmergencyPlanRequest } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { useOrders } from "@/lib/orders-context";
import { useMediaQuery } from "@/lib/use-media-query";
import { forecastFor, inventoryFor, isoPlusDays, parLevel, suppliers, type CertStatus } from "@/lib/mock-data";
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
  | { kind: "certificate"; status: CertStatus; authority: string; number: string }
  | { kind: "emergency"; drugName: string; surgePct: number; depletionDays: number | null; airFreightDays: number; costPremiumPct: number };

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  card?: ResponseCard;
};

let nextId = 1;
function id() {
  return `m-${nextId++}`;
}

const QUICK_ACTIONS = [
  { key: "po", label: "Generate PO", icon: FileText },
  { key: "analogue", label: "Find Bio-Equivalent", icon: Repeat2 },
  { key: "certificate", label: "Check Certificate", icon: ShieldCheck },
] as const;

// Reads the same mock-data every other page reads, keyed off whatever SKU
// is actually focused — the quick actions used to return fixed literals
// (a hardcoded PO regardless of drug, analogues from facilities that no
// longer exist) with no connection to what was on screen.
function replyFor(action: string, focus: CopilotFocus, facilityId: string): Message {
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
    return {
      id: id(),
      role: "assistant",
      text: `Certificate status for ${item.drugName}, verified against the manufacturer registry.`,
      card: { kind: "certificate", status: item.certStatus, authority: item.certAuthority, number: item.certNumber },
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
    return `Certificate Status\nStatus: ${card.status}\nAuthority: ${card.authority}\nNumber: ${card.number}`;
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
          <div className="flex justify-between"><span className="text-muted-foreground">Status</span><Badge variant={card.status === "valid" ? "default" : "secondary"} className="capitalize">{card.status}</Badge></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Authority</span><span>{card.authority}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Number</span><span className="font-mono text-xs">{card.number}</span></div>
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
  const [messages, setMessages] = useState<Message[]>([
    { id: id(), role: "assistant", text: "Hi, I'm the MedStock AI Copilot. Select a SKU or alert on the page, or ask me anything about inventory, forecasts, and shortages." },
  ]);
  const [draft, setDraft] = useState("");
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

  const focusLabel = focus?.label ?? "the current item";

  function runAction(action: string) {
    if (focus) {
      setMessages((m) => [...m, { id: id(), role: "user", text: `${QUICK_ACTIONS.find((a) => a.key === action)?.label} — ${focus.label}` }]);
    } else {
      setMessages((m) => [...m, { id: id(), role: "user", text: QUICK_ACTIONS.find((a) => a.key === action)?.label ?? action }]);
    }
    setPending(true);
    window.setTimeout(() => {
      setMessages((m) => [...m, replyFor(action, focus, facilityId)]);
      setPending(false);
    }, 300);
  }

  function clearConversation() {
    setMessages([
      { id: id(), role: "assistant", text: "Hi, I'm the MedStock AI Copilot. Select a SKU or alert on the page, or ask me anything about inventory, forecasts, and shortages." },
    ]);
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
      note: `Generated from ${card.confidence}% confidence forecast via AI Copilot.`,
    });
    setDraftedMessageIds((prev) => new Set(prev).add(messageId));
    toast.success(`Draft order ${order.id} created.`, {
      description: `${card.quantity} ${card.unit} of ${card.drugName} for ${facility.name}.`,
      action: { label: "Review", onClick: () => router.push("/orders") },
    });
  }

  function send() {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    // The textarea auto-grows with content; reset it back to one line once
    // the message is sent rather than leaving it at whatever height the
    // last message left it.
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setMessages((m) => [...m, { id: id(), role: "user", text }]);
    setPending(true);
    window.setTimeout(() => {
      setMessages((m) => [
        ...m,
        { id: id(), role: "assistant", text: `Looking into "${text}" for ${focusLabel}. In this demo I respond with canned guidance — try one of the quick actions above for a structured answer.` },
      ]);
      setPending(false);
    }, 300);
  }

  if (!open) {
    return (
      <div className="hidden shrink-0 border-l bg-card lg:flex lg:w-12 lg:flex-col lg:items-center lg:py-3">
        <Button variant="ghost" size="icon" onClick={() => setOpen(true)} aria-label="Open AI Copilot">
          <Bot />
        </Button>
      </div>
    );
  }

  const panel = (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Sparkles className="size-4 text-primary" />
          <span className="text-sm font-semibold">AI Copilot</span>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" onClick={clearConversation} aria-label="Clear conversation" disabled={messages.length <= 1}>
            <Eraser />
          </Button>
          <Button variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="Collapse AI Copilot">
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
              <div
                className={cn(
                  "max-w-[92%] rounded-lg px-3 py-2 text-sm",
                  m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted",
                )}
              >
                {m.role === "assistant" ? <StreamingText text={m.text} /> : m.text}
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
            placeholder="Ask the copilot…"
            className="min-h-9 resize-none py-2 text-sm"
            rows={1}
          />
          <Button size="icon" onClick={send} aria-label="Send message">
            <Send />
          </Button>
        </div>
        <span className="text-[10px] text-muted-foreground">Enter to send · Shift+Enter for a new line</span>
      </div>
    </div>
  );

  if (isDesktop) {
    return <aside className="flex min-h-0 w-[380px] shrink-0 flex-col border-l bg-card">{panel}</aside>;
  }

  return (
    <Sheet open onOpenChange={(next) => !next && setOpen(false)}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-md">
        <SheetTitle className="sr-only">AI Copilot</SheetTitle>
        {panel}
      </SheetContent>
    </Sheet>
  );
}
