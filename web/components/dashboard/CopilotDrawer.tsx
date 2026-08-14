"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Bot, FileText, Plane, Repeat2, ShieldCheck, Send, X, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { StatusBadge } from "@/components/dashboard/StatusBadge";
import { useCopilot, type EmergencyPlanRequest } from "@/lib/copilot-context";
import { cn } from "@/lib/utils";

type ResponseCard =
  | { kind: "po"; supplier: string; quantity: number; unit: string; totalCost: number; coverageDays: number }
  | { kind: "analogues"; items: { name: string; facility: string; stock: number }[] }
  | { kind: "certificate"; status: "valid" | "pending" | "expired"; authority: string; number: string }
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

function replyFor(action: string, focusLabel: string): Message {
  if (action === "po") {
    return {
      id: id(),
      role: "assistant",
      text: `Drafted a purchase order for ${focusLabel} based on the current burn rate and a 30-day coverage target.`,
      card: { kind: "po", supplier: "PharmaSource Global Ltd.", quantity: 150, unit: "boxes", totalCost: 1860, coverageDays: 30 },
    };
  }
  if (action === "analogue") {
    return {
      id: id(),
      role: "assistant",
      text: `Found bio-equivalent stock for ${focusLabel} across internal sub-stores, ranked by distance.`,
      card: {
        kind: "analogues",
        items: [
          { name: "Co-Amoxiclav 875/125mg", facility: "Sub-store B2", stock: 340 },
          { name: "Augmentin 875mg", facility: "Sub-store C1", stock: 96 },
        ],
      },
    };
  }
  if (action === "certificate") {
    return {
      id: id(),
      role: "assistant",
      text: `Certificate status for ${focusLabel}, verified against the manufacturer registry.`,
      card: { kind: "certificate", status: "valid", authority: "FDA", number: "NDA-050760-A2" },
    };
  }
  return {
    id: id(),
    role: "assistant",
    text: `I can help with ${focusLabel}. Ask about stock coverage, bio-equivalents, restock timing, or certificate status.`,
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
  return <p className="text-sm leading-relaxed">{shown}</p>;
}

function ResponseCardView({ card }: { card: ResponseCard }) {
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
          <div className="flex justify-between font-medium"><span className="text-muted-foreground font-normal">Est. total</span><span>${card.totalCost.toLocaleString()}</span></div>
        </CardContent>
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
                <p className="truncate text-xs text-muted-foreground">{it.facility}</p>
              </div>
              <Badge variant="secondary">{it.stock} in stock</Badge>
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
  const { open, setOpen, focus, emergencyRequest } = useCopilot();
  const [messages, setMessages] = useState<Message[]>([
    { id: id(), role: "assistant", text: "Hi, I'm the MedStock AI Copilot. Select a SKU or alert on the page, or ask me anything about inventory, forecasts, and shortages." },
  ]);
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const lastHandledNonce = useRef<number | null>(null);

  // A page (e.g. the forecast scenario simulator) can fire a one-shot
  // "emergency plan" ask via the copilot context — post it as a user
  // message and stream back the structured plan, same as a quick action.
  useEffect(() => {
    if (!emergencyRequest || emergencyRequest.nonce === lastHandledNonce.current) return;
    lastHandledNonce.current = emergencyRequest.nonce;
    setMessages((m) => [...m, { id: id(), role: "user", text: `Generate emergency supply plan for current load — ${emergencyRequest.drugName}` }]);
    window.setTimeout(() => setMessages((m) => [...m, emergencyPlanReply(emergencyRequest)]), 300);
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
    window.setTimeout(() => setMessages((m) => [...m, replyFor(action, focusLabel)]), 300);
  }

  function send() {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    setMessages((m) => [...m, { id: id(), role: "user", text }]);
    window.setTimeout(
      () =>
        setMessages((m) => [
          ...m,
          { id: id(), role: "assistant", text: `Looking into "${text}" for ${focusLabel}. In this demo I respond with canned guidance — try one of the quick actions above for a structured answer.` },
        ]),
      300,
    );
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

  return (
    <aside className="flex w-full min-h-0 shrink-0 flex-col border-l bg-card lg:w-[380px]">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Sparkles className="size-4 text-primary" />
          <span className="text-sm font-semibold">AI Copilot</span>
        </div>
        <Button variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="Collapse AI Copilot">
          <X />
        </Button>
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
        <div className="flex flex-col gap-3">
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
              {m.card && <div className="w-[92%]"><ResponseCardView card={m.card} /></div>}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      <Separator />
      <div className="flex items-end gap-2 p-3">
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
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
    </aside>
  );
}
