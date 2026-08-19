"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import type { DateRange } from "react-day-picker";
import { toast } from "sonner";
import {
  Search,
  SearchX,
  Plus,
  CalendarIcon,
  ChevronDown,
  MoreHorizontal,
  FileText,
  Repeat2,
  ScrollText,
  TrendingUp,
  Boxes,
  AlertTriangle,
  Clock,
  ShieldAlert,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Calendar } from "@/components/ui/calendar";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { StatTile } from "@/components/dashboard/StatTile";
import { SortableHead, nextSortState, compareValues, type SortState } from "@/components/dashboard/SortableHead";
import {
  CertificationBadge,
  recheckCertification,
  useCertificateDetail,
  useCertificationStatuses,
  useRuleset,
  type CertResult,
} from "@/components/CertificationBadge";
import { explainCertification, exploreStance, gatesFor, type Gate } from "@/lib/certification";
import { apiFetch } from "@/lib/api";
import { parseDrugName } from "@/lib/drug-name";
import { useCopilot } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { useInventory, type ShelfItem, type ShelfStatus } from "@/lib/inventory-context";
import { useSession } from "@/lib/session";
import { can } from "@/lib/rbac";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<ShelfStatus, string> = {
  stockout: "Stockout",
  critical: "Critical",
  normal: "Normal",
  surplus: "Surplus",
};

const STATUS_TONE: Record<ShelfStatus, StatusTone> = {
  stockout: "critical",
  critical: "critical",
  normal: "normal",
  surplus: "surplus",
};

function expiryTone(days: number | null): StatusTone {
  if (days == null) return "neutral";
  if (days <= 14) return "critical";
  if (days <= 30) return "warning";
  return "normal";
}

function isoToday(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  return Math.round((new Date(iso + "T00:00:00Z").getTime() - Date.now()) / 86_400_000);
}

type SortKey = "drugName" | "batchNumber" | "stock" | "risk" | "expiry";

// Words that appear in RxNorm display names but should not drive the
// inventory filter — otherwise "Oral Tablet" would match half the shelf.
const FILTER_NOISE = new Set([
  "mg",
  "mcg",
  "ug",
  "ml",
  "g",
  "l",
  "iu",
  "meq",
  "oral",
  "tablet",
  "tablets",
  "capsule",
  "capsules",
  "injection",
  "injectable",
  "solution",
  "suspension",
  "cream",
  "ointment",
  "gel",
  "patch",
  "pack",
  "packs",
  "film",
  "coated",
  "chewable",
  "delayed",
  "extended",
  "release",
  "and",
  "with",
  "for",
]);

function significantFilterTokens(query: string): string[] {
  return query
    .toLowerCase()
    .split(/[\s/,+()]+/)
    .map((token) => token.replace(/[^a-z0-9-]/g, ""))
    .filter((token) => token.length >= 4 && !FILTER_NOISE.has(token) && !/^\d/.test(token));
}

function itemMatchesInventoryQuery(
  item: ShelfItem,
  search: string,
  rxcuiNdcs: Set<string> | null,
): boolean {
  const q = search.trim().toLowerCase();
  if (!q && !rxcuiNdcs) return true;
  if (rxcuiNdcs?.has(item.ndc)) return true;
  if (!q) return false;
  const name = (item.name ?? "").toLowerCase();
  if (name.includes(q) || item.ndc.toLowerCase().includes(q)) return true;
  const tokens = significantFilterTokens(q);
  if (tokens.length === 0) return false;
  return tokens.some((token) => name.includes(token) || item.ndc.toLowerCase().includes(token));
}

function shelfStatusTone(status: string): StatusTone {
  return STATUS_TONE[status as ShelfStatus] ?? "neutral";
}

function shelfStatusLabel(status: string): string {
  return STATUS_LABEL[status as ShelfStatus] ?? status;
}

function sortValue(item: ShelfItem, key: SortKey): string | number {
  switch (key) {
    case "drugName": return parseDrugName(item.name ?? item.ndc).primary.toLowerCase();
    case "batchNumber": return item.lot ?? "";
    case "stock": return item.quantity;
    case "risk": return item.status === "stockout" ? 0 : item.status === "critical" ? 1 : item.status === "surplus" ? 2 : 3;
    case "expiry": return item.earliest_expiry ?? "9999-12-31";
  }
}

function ReceiveBatchDialog({ items }: { items: ShelfItem[] }) {
  const { receiveBatch } = useInventory();
  const [open, setOpen] = useState(false);
  const [ndc, setNdc] = useState<string>(items[0]?.ndc ?? "");
  const [batchNumber, setBatchNumber] = useState("");
  const [quantity, setQuantity] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [saving, setSaving] = useState(false);

  function reset() {
    setNdc(items[0]?.ndc ?? "");
    setBatchNumber("");
    setQuantity("");
    setExpiryDate("");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const qty = Number(quantity);
    const selected = items.find((i) => i.ndc === ndc);
    if (!qty || qty < 1 || !batchNumber.trim() || !expiryDate || !selected) return;
    setSaving(true);
    try {
      await receiveBatch({
        ndc: selected.ndc,
        lot: batchNumber.trim(),
        quantity: qty,
        expiryDate,
        location_id: selected.location_id,
      });
      setOpen(false);
      reset();
      toast.success("Batch received into inventory.", {
        description: `${qty} of ${selected.name ?? selected.ndc} — batch ${batchNumber.trim()}.`,
      });
    } catch (err) {
      toast.error("Could not receive batch", {
        description: err instanceof Error ? err.message : "inventory write failed",
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" className="h-8 text-xs">
          <Plus data-icon="inline-start" />
          Receive Batch
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Receive Batch</DialogTitle>
          <DialogDescription>Log a new batch into the current facility&apos;s stock.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit}>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="rb-item">Drug</FieldLabel>
              <Select value={ndc} onValueChange={setNdc}>
                <SelectTrigger id="rb-item" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {items.map((i) => (
                      <SelectItem key={i.ndc} value={i.ndc}>{i.name ?? i.ndc}</SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor="rb-batch">Batch #</FieldLabel>
              <Input
                id="rb-batch"
                value={batchNumber}
                onChange={(e) => setBatchNumber(e.target.value)}
                placeholder="e.g. AMX-24118-B"
                required
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="rb-qty">Quantity</FieldLabel>
              <Input
                id="rb-qty"
                type="number"
                min={1}
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                placeholder="0"
                required
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="rb-expiry">Expiry date</FieldLabel>
              <Input
                id="rb-expiry"
                type="date"
                min={isoToday()}
                value={expiryDate}
                onChange={(e) => setExpiryDate(e.target.value)}
                required
              />
            </Field>
          </FieldGroup>
          <DialogFooter className="mt-4">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button type="submit" disabled={saving || items.length === 0}>{saving ? "Saving…" : "Save batch"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

const SEVERITY_TONE: Record<string, StatusTone> = {
  red: "critical",
  yellow: "warning",
  info: "neutral",
};

const GATE_STYLE: Record<Gate["verdict"], { dot: string; word: string; box: string }> = {
  pass: { dot: "bg-emerald-500", word: "pass", box: "border-emerald-200 dark:border-emerald-500/25" },
  yellow: { dot: "bg-amber-500", word: "flagged", box: "border-amber-300 dark:border-amber-500/35" },
  red: { dot: "bg-red-500", word: "failed", box: "border-red-300 dark:border-red-500/35" },
  // Grey and explicitly worded. A row of green gates over a grey badge would be
  // the most misleading thing on this page: it would claim five checks cleared
  // for a drug nobody looked at.
  "not-run": { dot: "bg-muted-foreground/40", word: "not run", box: "border-dashed" },
};

/** The five questions behind one colour, and which of them this drug got through. */
function GateStrip({ gates }: { gates: Gate[] }) {
  if (gates.length === 0) return null;
  return (
    <ol className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-5">
      {gates.map((gate) => {
        const style = GATE_STYLE[gate.verdict];
        return (
          <li key={gate.category} className={cn("rounded-md border p-2", style.box)}>
            <div className="flex items-center gap-1.5">
              <span className={cn("size-1.5 shrink-0 rounded-full", style.dot)} />
              <span className="font-mono text-[10px] font-semibold uppercase tracking-wide">
                {gate.category}
              </span>
            </div>
            <p className="mt-1 text-[10px] leading-snug text-muted-foreground">{gate.question}</p>
            {/* The word carries the verdict, not the dot — the colours here are
                red and green next to each other, which is the one pair a
                red/green reader cannot separate. */}
            <p className="mt-1 font-mono text-[10px]">
              {style.word}
              <span className="text-muted-foreground">
                {" · "}
                {gate.fired.length > 0 ? `${gate.fired.length}/${gate.rules}` : `0/${gate.rules}`}
              </span>
            </p>
          </li>
        );
      })}
    </ol>
  );
}

// The evidence behind one colour (COMP-1), not a stand-in for a scanned PDF.
// Every finding names the FDA dataset it came from and links to it, because the
// answer to "why is my drug amber?" has to be checkable by the pharmacist who
// disagrees with it — see docs/compliance-usecases.md §3.
function CertificateDialog({
  item,
  open,
  onOpenChange,
}: {
  item: { drugName: string; ndc: string } | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  // Only fetch while the dialog is actually open: on a miss this endpoint
  // triggers COMP-2 exploration upstream, which spends real request budget.
  const { detail, error, loading, reload } = useCertificateDetail(
    open && item?.ndc ? item.ndc : null,
  );
  const ruleset = useRuleset();
  const { user } = useSession();
  const stance = exploreStance(user?.role);
  const [rechecking, setRechecking] = useState(false);

  // Re-runs the gates against freshly fetched upstream data, then reloads the
  // verdict. Two calls rather than one because /explore returns the row it
  // wrote and /certificates returns the findings behind it — reusing the read
  // path means the dialog cannot drift from what a reopen would show.
  async function recheck() {
    if (!item?.ndc) return;
    setRechecking(true);
    try {
      await recheckCertification(item.ndc);
      reload();
      toast.success("Re-checked against the FDA record.");
    } catch (e) {
      // Deliberately does not reload on failure: leaving the previous verdict
      // on screen under an error is honest, whereas blanking it would imply
      // the drug had become unknown when nothing about it changed.
      toast.error("Could not re-check", {
        description: e instanceof Error ? e.message : "upstream lookup failed",
      });
    } finally {
      setRechecking(false);
    }
  }

  // Built here rather than inline so the wording has one home: the copilot
  // drawer shows the same verdict, and two copies would eventually disagree
  // about what green means on the same screen.
  const why = explainCertification(detail, ruleset, { unreachable: Boolean(error) });
  if (!item) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Certification — {item.drugName}</DialogTitle>
          <DialogDescription>
            {item.ndc ? (
              <>NDC <span className="font-mono">{item.ndc}</span></>
            ) : (
              "No NDC recorded for this batch — nothing to certify against."
            )}
          </DialogDescription>
        </DialogHeader>

        {!item.ndc && (
          <div className="rounded-md border border-dashed p-6 text-center">
            <FileText className="mx-auto size-8 text-muted-foreground" />
            <p className="mt-2 text-xs text-muted-foreground">
              This batch was received as free text. Record an NDC to have it certified.
            </p>
          </div>
        )}

        {item.ndc && loading && <p className="text-xs text-muted-foreground">Checking FDA records…</p>}

        {item.ndc && error && (
          <div className="rounded-md border border-dashed p-6 text-center">
            <p className="text-xs font-medium">Compliance service unreachable</p>
            <p className="mt-1 text-[11px] text-muted-foreground">{error}</p>
            <p className="mt-2 text-[11px] text-muted-foreground">{why.caveat}</p>
          </div>
        )}

        {detail && (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <CertificationBadge result={{ status: detail.status, reasons: detail.findings.length }} />
              {detail.labeler && <span className="text-xs text-muted-foreground">{detail.labeler}</span>}
              {detail.marketing_category && (
                <span className="rounded border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                  {detail.marketing_category}
                </span>
              )}
            </div>

            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] sm:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">Listing expires</dt>
                <dd className="font-mono">{detail.listing_expiration_date ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Marketing ends</dt>
                <dd className="font-mono">{detail.marketing_end_date ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Checked</dt>
                <dd className="font-mono">{detail.computed_at?.slice(0, 16).replace("T", " ") ?? "—"}</dd>
              </div>
            </dl>

            {/* The five questions behind the one word on the badge. */}
            <GateStrip gates={gatesFor(detail, ruleset)} />

            {/* Why this colour — the part a pharmacist came here for. Stated
                for green as loudly as for red: an empty findings list reads
                exactly like "nobody looked", and those are different facts. */}
            <div className="rounded-md border bg-muted/40 p-3">
              <p className="text-xs font-medium">{why.headline}</p>
              <p className="mt-1 text-[11px] text-muted-foreground">{why.caveat}</p>

              {/* Green only: name the checks that ran. "Nothing was wrong" is an
                  assertion; "these 5 categories were evaluated" is evidence. */}
              {detail.status === "green" && why.checked.length > 0 && (
                <ul className="mt-2 flex flex-wrap gap-1">
                  {why.checked.map((c) => (
                    <li
                      key={c.category}
                      className="rounded border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                    >
                      {c.category} · {c.rules}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {detail.findings.length === 0 ? (
              <p className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">
                No finding on record — nothing fired, at any severity.
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {detail.findings.map((f, i) => (
                  <li key={`${f.code}-${i}`} className="rounded-md border p-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge tone={SEVERITY_TONE[f.severity] ?? "neutral"}>{f.code}</StatusBadge>
                      {/* Severity ordering alone leaves the reader to work out
                          which finding set the colour. Say it. */}
                      {why.decisive.some((d) => d.code === f.code) && (
                        <span className="rounded border border-current px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide">
                          sets the colour
                        </span>
                      )}
                      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                        {f.category}
                      </span>
                      {/* Standing vs transient is the distinction that keeps this
                          list actionable: a recall will clear, a dead listing
                          will not. */}
                      <span className="text-[10px] text-muted-foreground">
                        {f.transient ? "transient" : "standing"}
                      </span>
                    </div>
                    <p className="mt-1.5 text-xs">{f.message}</p>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      {f.source}
                      {f.source_ref ? ` · ${f.source_ref}` : ""}
                      {f.source_url && (
                        <>
                          {" · "}
                          <a
                            href={f.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="underline underline-offset-2"
                          >
                            source
                          </a>
                        </>
                      )}
                    </p>
                  </li>
                ))}
              </ul>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-2">
              <p className="text-[10px] text-muted-foreground">
                Ruleset {detail.ruleset_version ?? "—"}
                {detail.provenance ? ` · ${detail.provenance}` : ""}
              </p>
              {/* Offered when the role cannot be confirmed, same as the
                  prognosis controls: gating on auth being reachable would put
                  auth back in the critical path of a page built not to need it.
                  A 403 comes back as a toast, and the server stays the
                  authority. */}
              {stance !== "denied" && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={rechecking || loading}
                  onClick={recheck}
                >
                  <RefreshCw className={cn("size-3.5", rechecking && "animate-spin")} />
                  {rechecking ? "Re-checking…" : "Re-check now"}
                </Button>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function InventoryPage() {
  const router = useRouter();
  const { setFocus } = useCopilot();
  const { user } = useSession();
  const { facility } = useFacility();
  const { items, loading, error } = useInventory();
  const [search, setSearch] = useState("");
  const [rxcuiFilter, setRxcuiFilter] = useState<string | null>(null);
  const [rxcuiNdcs, setRxcuiNdcs] = useState<Set<string> | null>(null);
  const [status, setStatus] = useState<"all" | ShelfStatus>("all");
  const [range, setRange] = useState<DateRange | undefined>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [certItem, setCertItem] = useState<{ drugName: string; ndc: string } | null>(null);
  const [sort, setSort] = useState<SortState<SortKey>>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const name = params.get("name") ?? "";
    const rxcui = params.get("rxcui");
    if (name) setSearch(name);
    if (rxcui) setRxcuiFilter(rxcui);
  }, []);

  useEffect(() => {
    if (!rxcuiFilter) {
      setRxcuiNdcs(null);
      return;
    }
    let cancelled = false;
    apiFetch("inventory", `/stock?rxcui=${encodeURIComponent(rxcuiFilter)}&facility_id=${facility.id}`)
      .then((body: { items?: { ndc: string }[] }) => {
        if (cancelled) return;
        setRxcuiNdcs(new Set((body.items ?? []).map((row) => row.ndc)));
      })
      .catch(() => {
        if (!cancelled) setRxcuiNdcs(new Set());
      });
    return () => {
      cancelled = true;
    };
  }, [rxcuiFilter, facility.id]);

  const certNdcs = useMemo(() => items.map((i) => i.ndc).filter(Boolean), [items]);
  const certification = useCertificationStatuses(certNdcs);

  const certFor = (item: ShelfItem): CertResult | undefined =>
    item.ndc ? certification[item.ndc] : { status: "unknown", reasons: 0 };

  const kpis = useMemo(() => {
    return {
      totalSkus: items.length,
      criticalStock: items.filter((i) => i.status === "critical" || i.status === "stockout").length,
      expiringSoon: items.filter((i) => {
        const d = daysUntil(i.earliest_expiry);
        return d != null && d <= 30;
      }).length,
      certAlerts: items.filter((i) => {
        const s = i.ndc ? certification[i.ndc]?.status : undefined;
        return s === "yellow" || s === "red";
      }).length,
    };
  }, [items, certification]);

  const filtered = useMemo(() => {
    return items.filter((item) => {
      if (!itemMatchesInventoryQuery(item, search, rxcuiNdcs)) return false;
      if (status !== "all" && item.status !== status) return false;
      if (range?.from && item.earliest_expiry) {
        const expiry = new Date(item.earliest_expiry);
        if (expiry < range.from) return false;
        if (range.to && expiry > range.to) return false;
      } else if (range?.from && !item.earliest_expiry) {
        return false;
      }
      return true;
    });
  }, [items, search, rxcuiNdcs, status, range]);

  function clearInventoryDeepLink() {
    if (!rxcuiFilter && !window.location.search) return;
    setRxcuiFilter(null);
    if (window.location.search) {
      router.replace("/inventory", { scroll: false });
    }
  }

  function onSearchChange(value: string) {
    setSearch(value);
    clearInventoryDeepLink();
  }

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const dir = sort.direction === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => dir * compareValues(sortValue(a, sort.key), sortValue(b, sort.key)));
  }, [filtered, sort]);

  function selectRow(item: ShelfItem) {
    const next = selectedId === item.ndc ? null : item.ndc;
    setSelectedId(next);
    if (next) {
      setFocus({
        kind: "sku",
        label: item.name ?? item.ndc,
        detail: `Lot ${item.lot ?? "—"} · ${item.quantity} on hand · expires ${item.earliest_expiry ?? "—"}`,
        itemId: item.ndc,
        ndc: item.ndc,
        rxcui: item.rxcui ?? null,
      });
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 p-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Inventory & Batches</h1>
        <p className="text-xs text-muted-foreground">
          Stock on hand, batch traceability, and certificate status at{" "}
          <span className="font-medium text-foreground">{facility.name}</span>.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile icon={Boxes} label="SKUs this site" value={kpis.totalSkus.toLocaleString()} />
        <StatTile icon={AlertTriangle} label="Critical / stockout" value={kpis.criticalStock} tone="critical" />
        <StatTile icon={Clock} label="Expiring soon (<30d)" value={kpis.expiringSoon} tone="warning" />
        <StatTile icon={ShieldAlert} label="Certification alerts" value={kpis.certAlerts} tone="warning" />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Filter by name, NDC, or analogue…"
            className="h-8 w-80 pl-8 text-xs"
          />
        </div>
        {search.trim() || rxcuiFilter ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 text-xs"
            onClick={() => {
              setSearch("");
              clearInventoryDeepLink();
            }}
          >
            Clear filter
          </Button>
        ) : null}

        <Select value={status} onValueChange={(v) => setStatus(v as typeof status)}>
          <SelectTrigger size="sm" className="h-8 w-36 text-xs">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="stockout">Stockout</SelectItem>
              <SelectItem value="critical">Critical</SelectItem>
              <SelectItem value="normal">Normal</SelectItem>
              <SelectItem value="surplus">Surplus</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>

        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs font-normal">
              <CalendarIcon className="size-3.5" />
              {range?.from ? (range.to ? `${range.from.toLocaleDateString()} – ${range.to.toLocaleDateString()}` : range.from.toLocaleDateString()) : "Expiry date range"}
              <ChevronDown className="size-3.5 text-muted-foreground" />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <Calendar mode="range" selected={range} onSelect={setRange} numberOfMonths={2} />
            {range?.from && (
              <div className="border-t p-2">
                <Button variant="ghost" size="sm" className="h-7 w-full text-xs" onClick={() => setRange(undefined)}>Clear</Button>
              </div>
            )}
          </PopoverContent>
        </Popover>

        {can(user?.role, "receiveBatch") && (
          <div className="ml-auto">
            <ReceiveBatchDialog items={items} />
          </div>
        )}
      </div>

      <p className="text-[11px] text-muted-foreground">
        Showing {filtered.length} of {items.length} SKUs stocked at {facility.name}
        {filtered.length !== items.length ? " · filters applied" : ""}.
      </p>

      <div className="min-h-0 flex-1 overflow-hidden rounded-lg border bg-card">
        {/* Table's own wrapper is `overflow-x-auto`, which forces its
            overflow-y to compute as "auto" too (CSS Overflow §3: if one
            axis is non-visible, "visible" on the other computes to "auto")
            — that stray, unbounded vertical scrollbox, not the page's real
            scroll, was what the sticky header was binding to, so it never
            actually stuck. Bounding its height via containerClassName makes
            it a real scroll container the header can stick within.
            Filling the remaining page height (rather than a fixed max-h)
            means this is the page's only scroll container — a fixed cap
            plus the page's own overflow-y-auto produced two independently
            scrolling bars stacked at the same edge. */}
          <Table containerClassName="h-full overflow-auto" className="min-w-[52rem]">
            <TableHeader className="sticky top-0 z-10 border-b bg-card">
              <TableRow>
                <SortableHead sortKey="drugName" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Drug Name & Form</SortableHead>
                <SortableHead sortKey="batchNumber" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Lot</SortableHead>
                <SortableHead sortKey="stock" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Stock</SortableHead>
                <SortableHead sortKey="risk" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Status</SortableHead>
                <SortableHead sortKey="expiry" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Expiry</SortableHead>
                <TableHead>Certificate</TableHead>
                <TableHead className="sticky right-0 z-20 w-10 bg-card shadow-[-8px_0_8px_-8px_rgba(0,0,0,0.18)]" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((item) => {
                const expiryDays = daysUntil(item.earliest_expiry);
                const selected = selectedId === item.ndc;
                const label = parseDrugName(item.name ?? item.ndc);
                return (
                    <TableRow
                      key={item.ndc}
                      onClick={() => selectRow(item)}
                      aria-selected={selected}
                      className={cn("group cursor-pointer text-xs", selected && "bg-muted/60")}
                    >
                      <TableCell className="max-w-[18rem] whitespace-normal py-2 font-medium">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            selectRow(item);
                          }}
                          className="rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {label.primary}
                          {label.detail && (
                            <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">{label.detail}</span>
                          )}
                          {item.in_formulary ? (
                            <Badge variant="secondary" className="ml-1.5 text-[10px] font-normal">
                              formulary
                            </Badge>
                          ) : null}
                          <span className="block font-mono text-[10px] font-normal text-muted-foreground">{item.ndc}</span>
                        </button>
                      </TableCell>
                      <TableCell className="py-2 font-mono text-[11px] tabular-nums">{item.lot ?? "—"}</TableCell>
                      <TableCell className="py-2 font-mono tabular-nums">
                        {item.quantity}
                        <span className="font-sans text-muted-foreground"> on hand</span>
                        {item.par_defined && item.reorder_point != null && (
                          <span className="block font-sans text-[10px] font-normal text-muted-foreground">
                            Reorder at {item.reorder_point}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="py-2">
                        <StatusBadge tone={shelfStatusTone(item.status)}>
                          {shelfStatusLabel(item.status)} · {item.quantity}
                          {!item.par_defined ? " · no par" : ""}
                        </StatusBadge>
                      </TableCell>
                      <TableCell className="py-2">
                        {item.earliest_expiry ? (
                          <StatusBadge tone={expiryTone(expiryDays)}>
                            {item.earliest_expiry} ({expiryDays}d)
                          </StatusBadge>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="py-2" onClick={(e) => e.stopPropagation()}>
                        <CertificationBadge
                          result={certFor(item)}
                          label={label.primary}
                          onClick={() => setCertItem({ drugName: label.primary, ndc: item.ndc })}
                        />
                      </TableCell>
                      <TableCell
                        className={cn(
                          "sticky right-0 z-10 py-2 shadow-[-8px_0_8px_-8px_rgba(0,0,0,0.18)]",
                          selected ? "bg-muted" : "bg-card group-hover:bg-muted/50",
                        )}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="size-7">
                              <MoreHorizontal className="size-4" />
                              <span className="sr-only">Actions for {label.primary}</span>
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-auto min-w-48">
                            <DropdownMenuGroup>
                              <DropdownMenuItem
                                onSelect={() => {
                                  selectRow(item);
                                  router.push(`/analogue?q=${encodeURIComponent(label.raw)}`);
                                }}
                              >
                                <Repeat2 /> Find analogues
                              </DropdownMenuItem>
                              <DropdownMenuItem onSelect={() => setCertItem({ drugName: label.primary, ndc: item.ndc })}>
                                <FileText /> View certificate
                              </DropdownMenuItem>
                              <DropdownMenuItem onSelect={() => router.push(`/forecasts?sku=${item.ndc}`)}>
                                <TrendingUp /> View forecast
                              </DropdownMenuItem>
                              <DropdownMenuItem onSelect={() => router.push(`/audit?sku=${item.ndc}`)}>
                                <ScrollText /> Audit Log
                              </DropdownMenuItem>
                            </DropdownMenuGroup>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                );
              })}
              {filtered.length === 0 && (
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={7} className="py-10 text-center">
                    <SearchX className="mx-auto size-6 text-muted-foreground/50" />
                    <p className="mt-2 text-xs font-medium">
                      {loading ? "Loading shelf…" : error ? "Cannot load inventory" : "No SKUs match the current filters"}
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      {error
                        ? error
                        : search.trim() || rxcuiFilter
                          ? "Try clearing the search box, status filter, or expiry date range."
                          : "Try clearing the status filter or expiry date range."}
                    </p>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
      </div>

      <CertificateDialog
        item={certItem}
        open={certItem !== null}
        onOpenChange={(o) => !o && setCertItem(null)}
      />
    </div>
  );
}
