"use client";

import { useMemo, useState } from "react";
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
import { useSession } from "@/lib/session";
import { useCopilot } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import { useInventory } from "@/lib/inventory-context";
import { useSession } from "@/lib/session";
import { can } from "@/lib/rbac";
import {
  inventoryKpisFor,
  isoPlusDays,
  daysOfSupply,
  reorderPoint,
  stockRisk,
  daysUntil,
  type InventoryItem,
  type StockRisk,
} from "@/lib/mock-data";
import { cn } from "@/lib/utils";

const RISK_LABEL: Record<StockRisk, string> = { critical: "Critical", warning: "Warning", normal: "Normal" };

function expiryTone(days: number): StatusTone {
  if (days <= 14) return "critical";
  if (days <= 30) return "warning";
  return "normal";
}

const OTHER_SKU = "__other__";

type SortKey = "drugName" | "batchNumber" | "stock" | "burn" | "risk" | "expiry";

// Risk sorts by days-of-supply (the number actually driving the badge), not
// the tone label — "most urgent first" is what a pharmacist wants from this
// column, and daysOfSupply is what "urgent" means here.
function sortValue(item: InventoryItem, key: SortKey): string | number {
  switch (key) {
    case "drugName": return item.drugName;
    case "batchNumber": return item.batchNumber;
    case "stock": return item.currentStock;
    case "burn": return item.dailyBurnRate;
    case "risk": return Number.isFinite(daysOfSupply(item)) ? daysOfSupply(item) : Infinity;
    case "expiry": return item.expiryDate;
  }
}

// Actually writes into inventory now — previously validated a full form
// and then discarded it, with a success toast in front of a no-op. A
// picker over the facility's own catalogue is the default (free text can't
// resolve to a real InventoryItem); "Other / new SKU" is the escape hatch
// for a genuinely new product.
function ReceiveBatchDialog({ facilityId, items }: { facilityId: string; items: InventoryItem[] }) {
  const { receiveBatch } = useInventory();
  const [open, setOpen] = useState(false);
  const [itemId, setItemId] = useState<string>(items[0]?.id ?? OTHER_SKU);
  const [drugName, setDrugName] = useState("");
  const [batchNumber, setBatchNumber] = useState("");
  const [quantity, setQuantity] = useState("");
  const [expiryDate, setExpiryDate] = useState("");

  function reset() {
    setItemId(items[0]?.id ?? OTHER_SKU);
    setDrugName("");
    setBatchNumber("");
    setQuantity("");
    setExpiryDate("");
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const qty = Number(quantity);
    const isNew = itemId === OTHER_SKU;
    if (!qty || qty < 1 || !batchNumber.trim() || !expiryDate || (isNew && !drugName.trim())) return;

    const selected = items.find((i) => i.id === itemId);
    const created = receiveBatch(facilityId, {
      itemId: isNew ? undefined : itemId,
      drugName: isNew ? drugName.trim() : selected!.drugName,
      batchNumber: batchNumber.trim(),
      quantity: qty,
      expiryDate,
    });
    setOpen(false);
    reset();
    toast.success("Batch received into inventory.", {
      description: `${qty} ${isNew ? "units" : selected!.unit} of ${created.drugName} — batch ${batchNumber.trim()}.`,
    });
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
              <Select value={itemId} onValueChange={setItemId}>
                <SelectTrigger id="rb-item" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {items.map((i) => (
                      <SelectItem key={i.id} value={i.id}>{i.drugName}</SelectItem>
                    ))}
                    <SelectItem value={OTHER_SKU}>Other / new SKU…</SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
            </Field>
            {itemId === OTHER_SKU && (
              <Field>
                <FieldLabel htmlFor="rb-drug">Drug name</FieldLabel>
                <Input
                  id="rb-drug"
                  value={drugName}
                  onChange={(e) => setDrugName(e.target.value)}
                  placeholder="e.g. Amoxicillin/Clavulanate 875mg"
                  required
                />
              </Field>
            )}
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
                min={isoPlusDays(0)}
                value={expiryDate}
                onChange={(e) => setExpiryDate(e.target.value)}
                required
              />
            </Field>
          </FieldGroup>
          <DialogFooter className="mt-4">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button type="submit">Save batch</Button>
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
  item: InventoryItem | null;
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
  const { facilityId, facility } = useFacility();
  const { itemsFor } = useInventory();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"all" | StockRisk>("all");
  const [range, setRange] = useState<DateRange | undefined>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [certItem, setCertItem] = useState<InventoryItem | null>(null);
  const [sort, setSort] = useState<SortState<SortKey>>(null);

  const items = useMemo(() => itemsFor(facilityId), [itemsFor, facilityId]);

  // COMP-1: one batched call for the whole shelf, not one per row. Fetched
  // separately from stock on purpose — compliance being down must never blank
  // the inventory table (docs/compliance-usecases.md §2.2).
  const certNdcs = useMemo(() => items.map((i) => i.ndc).filter(Boolean), [items]);
  const certification = useCertificationStatuses(certNdcs);

  // A row with no NDC is `unknown` (nothing to certify), not `unavailable`
  // (we tried and could not reach the service). Those are different facts and
  // the badge says so.
  const certFor = (item: InventoryItem): CertResult | undefined =>
    item.ndc ? certification[item.ndc] : { status: "unknown", reasons: 0 };
  // criticalStock/expiringSoon/certAlerts recomputed from `items` (the
  // received-batch overlay included) rather than read verbatim from
  // inventoryKpisFor, so topping up a critical SKU can move it out of that
  // count. totalSkus stays the network-wide catalogue figure — it was never
  // meant to be "rows in this table" (see UX-12).
  const kpis = useMemo(() => {
    const catalogue = inventoryKpisFor(facilityId);
    return {
      totalSkus: catalogue.totalSkus,
      criticalStock: items.filter((i) => stockRisk(i) === "critical").length,
      expiringSoon: items.filter((i) => daysUntil(i.expiryDate) <= 30).length,
      // Amber and red only. `unknown` and `unavailable` are deliberately not
      // counted here — a drug nobody has a record for, or one we could not
      // check because the service is down, is not the same alert as a live
      // recall, and folding them together would make this number jump to the
      // shelf size the moment compliance goes offline.
      certAlerts: items.filter((i) => {
        const s = i.ndc ? certification[i.ndc]?.status : undefined;
        return s === "yellow" || s === "red";
      }).length,
    };
  }, [items, facilityId, certification]);

  const filtered = useMemo(() => {
    return items.filter((item) => {
      const q = search.trim().toLowerCase();
      if (q && !(item.drugName.toLowerCase().includes(q) || item.inn.toLowerCase().includes(q) || item.atcCode.toLowerCase().includes(q))) {
        return false;
      }
      if (status !== "all" && stockRisk(item) !== status) return false;
      if (range?.from) {
        const expiry = new Date(item.expiryDate);
        if (expiry < range.from) return false;
        if (range.to && expiry > range.to) return false;
      }
      return true;
    });
  }, [items, search, status, range]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const dir = sort.direction === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => dir * compareValues(sortValue(a, sort.key), sortValue(b, sort.key)));
  }, [filtered, sort]);

  function selectRow(item: InventoryItem) {
    const next = selectedId === item.id ? null : item.id;
    setSelectedId(next);
    if (next) {
      setFocus({
        kind: "sku",
        label: item.drugName,
        detail: `Batch ${item.batchNumber} · ${item.currentStock} ${item.unit} on hand · expires ${item.expiryDate}`,
        itemId: item.id,
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
        <StatTile icon={Boxes} label="Catalogue SKUs" value={kpis.totalSkus.toLocaleString()} />
        <StatTile icon={AlertTriangle} label="Critical stock (<3d)" value={kpis.criticalStock} tone="critical" />
        <StatTile icon={Clock} label="Expiring soon (<30d)" value={kpis.expiringSoon} tone="warning" />
        <StatTile icon={ShieldAlert} label="Certification alerts" value={kpis.certAlerts} tone="warning" />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter by SKU, INN, or ATC code…"
            className="h-8 w-64 pl-8 text-xs"
          />
        </div>

        <Select value={status} onValueChange={(v) => setStatus(v as typeof status)}>
          <SelectTrigger size="sm" className="h-8 w-36 text-xs">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="critical">Critical</SelectItem>
              <SelectItem value="warning">Warning</SelectItem>
              <SelectItem value="normal">Normal</SelectItem>
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
            <ReceiveBatchDialog facilityId={facilityId} items={items} />
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
          <Table containerClassName="h-full overflow-auto">
            <TableHeader className="sticky top-0 z-10 border-b bg-card">
              <TableRow>
                <SortableHead sortKey="drugName" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Drug Name & Form</SortableHead>
                <SortableHead sortKey="batchNumber" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Batch #</SortableHead>
                <SortableHead sortKey="stock" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Stock</SortableHead>
                <SortableHead sortKey="burn" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Daily Burn</SortableHead>
                <SortableHead sortKey="risk" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Stockout Risk</SortableHead>
                <SortableHead sortKey="expiry" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Expiry</SortableHead>
                <TableHead>Certificate</TableHead>
                <TableHead className="w-8" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((item) => {
                const risk = stockRisk(item);
                const expiryDays = daysUntil(item.expiryDate);
                const selected = selectedId === item.id;
                return (
                    <TableRow
                      key={item.id}
                      onClick={() => selectRow(item)}
                      aria-selected={selected}
                      className={cn("cursor-pointer text-xs", selected && "bg-muted/60")}
                    >
                      <TableCell className="py-2 font-medium">
                        {/* A real button, not just a clickable <tr> — the
                            row's onClick is a mouse convenience, this is
                            what makes row selection (and the copilot
                            context it sets) reachable by keyboard. */}
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            selectRow(item);
                          }}
                          className="rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {item.drugName}
                          <span className="block font-normal text-muted-foreground">
                            {item.form} · {item.inn}
                          </span>
                          <span className="block font-mono text-[10px] text-muted-foreground">{item.atcCode}</span>
                        </button>
                      </TableCell>
                      <TableCell className="py-2 font-mono text-[11px] tabular-nums">{item.batchNumber}</TableCell>
                      <TableCell className="py-2 font-mono tabular-nums">
                        {item.currentStock} <span className="font-sans text-muted-foreground">{item.unit}</span>
                        <span className="block font-sans text-[10px] font-normal text-muted-foreground">
                          Reorder at {reorderPoint(item)}
                        </span>
                      </TableCell>
                      <TableCell className="py-2 font-mono tabular-nums text-muted-foreground">{item.dailyBurnRate}/day</TableCell>
                      <TableCell className="py-2">
                        <StatusBadge tone={risk}>
                          {RISK_LABEL[risk]} · {Number.isFinite(daysOfSupply(item)) ? `${daysOfSupply(item)}d` : "∞"}
                        </StatusBadge>
                      </TableCell>
                      <TableCell className="py-2">
                        <StatusBadge tone={expiryTone(expiryDays)}>
                          {item.expiryDate} ({expiryDays}d)
                        </StatusBadge>
                      </TableCell>
                      {/* The badge is the trigger. The reasoning used to live
                          behind the kebab menu, which is an odd place to hide
                          the answer to the question the colour itself provokes.
                          stopPropagation so it does not also select the row. */}
                      <TableCell className="py-2" onClick={(e) => e.stopPropagation()}>
                        <CertificationBadge
                          result={certFor(item)}
                          label={item.drugName}
                          onClick={() => setCertItem(item)}
                        />
                      </TableCell>
                      <TableCell className="py-2" onClick={(e) => e.stopPropagation()}>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="size-6">
                              <MoreHorizontal className="size-3.5" />
                              <span className="sr-only">Actions for {item.drugName}</span>
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuGroup>
                              <DropdownMenuItem
                                onSelect={() => {
                                  selectRow(item);
                                  router.push(`/analogue?q=${encodeURIComponent(item.drugName)}`);
                                }}
                              >
                                <Repeat2 /> Find analogues
                              </DropdownMenuItem>
                              <DropdownMenuItem onSelect={() => setCertItem(item)}>
                                <FileText /> View certificate
                              </DropdownMenuItem>
                              <DropdownMenuItem onSelect={() => router.push(`/forecasts?sku=${item.id}`)}>
                                <TrendingUp /> View forecast
                              </DropdownMenuItem>
                              <DropdownMenuItem onSelect={() => router.push(`/audit?sku=${item.id}`)}>
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
                  <TableCell colSpan={8} className="py-10 text-center">
                    <SearchX className="mx-auto size-6 text-muted-foreground/50" />
                    <p className="mt-2 text-xs font-medium">No SKUs match the current filters</p>
                    <p className="text-[11px] text-muted-foreground">Try clearing the status filter or expiry date range.</p>
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
