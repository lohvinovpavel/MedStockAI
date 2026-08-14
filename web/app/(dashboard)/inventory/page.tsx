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
  Boxes,
  AlertTriangle,
  Clock,
  ShieldAlert,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
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
import { AnaloguesDialog } from "@/components/dashboard/AnaloguesDialog";
import { useCopilot } from "@/lib/copilot-context";
import { useFacility } from "@/lib/facility-context";
import {
  inventoryFor,
  inventoryKpisFor,
  daysOfSupply,
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

const CERT_TONE: Record<InventoryItem["certStatus"], StatusTone> = {
  valid: "normal",
  pending: "warning",
  expired: "critical",
};

function KpiCard({ icon: Icon, label, value, tone }: { icon: typeof Boxes; label: string; value: string | number; tone?: "critical" | "warning" }) {
  return (
    <Card className="gap-1 py-3">
      <CardContent className="flex items-center gap-3 px-4">
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-md",
            tone === "critical" && "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400",
            tone === "warning" && "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400",
            !tone && "bg-muted text-muted-foreground",
          )}
        >
          <Icon className="size-4" />
        </span>
        <div>
          <p className="font-mono text-lg font-semibold leading-none tabular-nums">{value}</p>
          <p className="mt-1 text-xs text-muted-foreground">{label}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function ReceiveBatchDialog() {
  const [open, setOpen] = useState(false);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setOpen(false);
    toast.success("Batch received into inventory.");
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
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
              <FieldLabel htmlFor="rb-drug">Drug name</FieldLabel>
              <Input id="rb-drug" placeholder="e.g. Amoxicillin/Clavulanate 875mg" required />
            </Field>
            <Field>
              <FieldLabel htmlFor="rb-batch">Batch #</FieldLabel>
              <Input id="rb-batch" placeholder="e.g. AMX-24118-B" required />
            </Field>
            <Field>
              <FieldLabel htmlFor="rb-qty">Quantity</FieldLabel>
              <Input id="rb-qty" type="number" min={1} placeholder="0" required />
            </Field>
            <Field>
              <FieldLabel htmlFor="rb-expiry">Expiry date</FieldLabel>
              <Input id="rb-expiry" type="date" required />
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

function CertificateDialog({
  item,
  open,
  onOpenChange,
}: {
  item: InventoryItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  if (!item) return null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{item.certAuthority} Certificate — {item.drugName}</DialogTitle>
          <DialogDescription>Certificate #{item.certNumber}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col items-center gap-3 rounded-md border border-dashed p-8 text-center">
          <FileText className="size-10 text-muted-foreground" />
          <div>
            <p className="text-sm font-medium">{item.certAuthority}-{item.certNumber}.pdf</p>
            <p className="text-xs text-muted-foreground">Mock certificate preview — document viewer not wired in this demo.</p>
          </div>
          <StatusBadge tone={CERT_TONE[item.certStatus]}>{item.certStatus}</StatusBadge>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function InventoryPage() {
  const router = useRouter();
  const { setFocus } = useCopilot();
  const { facilityId, facility } = useFacility();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"all" | StockRisk>("all");
  const [range, setRange] = useState<DateRange | undefined>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [analogueItem, setAnalogueItem] = useState<InventoryItem | null>(null);
  const [certItem, setCertItem] = useState<InventoryItem | null>(null);

  const items = useMemo(() => inventoryFor(facilityId), [facilityId]);
  const kpis = useMemo(() => inventoryKpisFor(facilityId), [facilityId]);

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

  function selectRow(item: InventoryItem) {
    const next = selectedId === item.id ? null : item.id;
    setSelectedId(next);
    if (next) {
      setFocus({
        kind: "sku",
        label: item.drugName,
        detail: `Batch ${item.batchNumber} · ${item.currentStock} ${item.unit} on hand · expires ${item.expiryDate}`,
      });
    }
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Inventory & Batches</h1>
        <p className="text-xs text-muted-foreground">
          Stock on hand, batch traceability, and certificate status at{" "}
          <span className="font-medium text-foreground">{facility.name}</span>.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard icon={Boxes} label="Total SKUs" value={kpis.totalSkus.toLocaleString()} />
        <KpiCard icon={AlertTriangle} label="Critical stock (<3d)" value={kpis.criticalStock} tone="critical" />
        <KpiCard icon={Clock} label="Expiring soon (<30d)" value={kpis.expiringSoon} tone="warning" />
        <KpiCard icon={ShieldAlert} label="Pending certificates" value={kpis.pendingCerts} tone="warning" />
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

        <div className="ml-auto">
          <ReceiveBatchDialog />
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border bg-card">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader className="sticky top-0 z-10 bg-card">
              <TableRow>
                <TableHead>Drug Name & Form</TableHead>
                <TableHead>INN</TableHead>
                <TableHead>Batch #</TableHead>
                <TableHead>Stock</TableHead>
                <TableHead>Daily Burn</TableHead>
                <TableHead>Stockout Risk</TableHead>
                <TableHead>Expiry</TableHead>
                <TableHead>Certificate</TableHead>
                <TableHead className="w-8" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((item) => {
                const risk = stockRisk(item);
                const expiryDays = daysUntil(item.expiryDate);
                const selected = selectedId === item.id;
                return (
                    <TableRow
                      key={item.id}
                      onClick={() => selectRow(item)}
                      className={cn("cursor-pointer text-xs", selected && "bg-muted/60")}
                    >
                      <TableCell className="py-2 font-medium">
                        {item.drugName}
                        <span className="block font-normal text-muted-foreground">{item.form}</span>
                      </TableCell>
                      <TableCell className="py-2 text-muted-foreground">{item.inn}</TableCell>
                      <TableCell className="py-2 font-mono text-[11px] tabular-nums">{item.batchNumber}</TableCell>
                      <TableCell className="py-2 font-mono tabular-nums">{item.currentStock} <span className="font-sans text-muted-foreground">{item.unit}</span></TableCell>
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
                      <TableCell className="py-2">
                        <StatusBadge tone={CERT_TONE[item.certStatus]} className="capitalize">
                          {item.certAuthority} · {item.certStatus}
                        </StatusBadge>
                      </TableCell>
                      <TableCell className="py-2" onClick={(e) => e.stopPropagation()}>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="size-6">
                              <MoreHorizontal className="size-3.5" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuGroup>
                              <DropdownMenuItem onSelect={() => { selectRow(item); setAnalogueItem(item); }}>
                                <Repeat2 /> Find analogues
                              </DropdownMenuItem>
                              <DropdownMenuItem onSelect={() => setCertItem(item)}>
                                <FileText /> View certificate
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
                  <TableCell colSpan={9} className="py-10 text-center">
                    <SearchX className="mx-auto size-6 text-muted-foreground/50" />
                    <p className="mt-2 text-xs font-medium">No SKUs match the current filters</p>
                    <p className="text-[11px] text-muted-foreground">Try clearing the status filter or expiry date range.</p>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      <AnaloguesDialog
        item={analogueItem}
        open={analogueItem !== null}
        onOpenChange={(o) => !o && setAnalogueItem(null)}
      />
      <CertificateDialog
        item={certItem}
        open={certItem !== null}
        onOpenChange={(o) => !o && setCertItem(null)}
      />
    </div>
  );
}
