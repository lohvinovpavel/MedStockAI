"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  CheckCircle2,
  ClipboardList,
  Inbox,
  PackageCheck,
  Sparkles,
  Truck,
  Wallet,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/ui/table";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { StatTile } from "@/components/dashboard/StatTile";
import { SortableHead, nextSortState, compareValues, type SortState } from "@/components/dashboard/SortableHead";
import { useFacility } from "@/lib/facility-context";
import { useOrders, type OrderListItem, type OrderStatus } from "@/lib/orders-context";
import { useSession } from "@/lib/session";
import { can } from "@/lib/rbac";
import { apiFetch } from "@/lib/api";

const STATUS_TONE: Record<OrderStatus, StatusTone> = {
  draft: "warning",
  placed: "surplus",
  in_transit: "surplus",
  delivered: "normal",
  cancelled: "critical",
};

const STATUS_LABEL: Record<OrderStatus, StatusTone extends never ? string : string> = {
  draft: "Draft",
  placed: "Placed",
  in_transit: "In transit",
  delivered: "Delivered",
  cancelled: "Cancelled",
};

type SupplierRow = {
  id: number;
  name: string;
  lead_time_days: number;
  shipping_flat: number;
  active: boolean;
};

type CatalogRow = { ndc: string; unit_cost: number; pack_size: number; min_order_qty: number };

type QuoteBody = {
  subtotal: number;
  shipping: number;
  total: number;
  lead_time_days: number;
  expected_delivery: string;
};

type FormItem = { ndc: string; name: string | null };

function money(n: number) {
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

type SortKey = "id" | "createdAt" | "facility" | "supplier" | "drug" | "qty" | "total" | "source" | "status";

function sortValue(o: OrderListItem, key: SortKey): string | number {
  switch (key) {
    case "id": return o.ref;
    case "createdAt": return o.created_at ? new Date(o.created_at).getTime() : 0;
    case "facility": return o.facility.name ?? "";
    case "supplier": return o.supplier.name ?? "";
    case "drug": return o.primary_drug ?? "";
    case "qty": return o.quantity;
    case "total": return o.total;
    case "source": return o.source;
    case "status": return STATUS_LABEL[o.status];
  }
}

export default function OrdersPage() {
  const { user } = useSession();
  const canPlace = can(user?.role, "placeOrder");
  const { facility, operatedFacilities } = useFacility();
  const { orders, summary, createOrder, placeOrder, discardDraft } = useOrders();

  const [formFacilityPk, setFormFacilityPk] = useState(facility.id);
  const [supplierId, setSupplierId] = useState<number | undefined>();
  const [ndc, setNdc] = useState<string | undefined>();
  const [quantity, setQuantity] = useState(100);
  const [statusFilter, setStatusFilter] = useState<"all" | OrderStatus>("all");
  const [sort, setSort] = useState<SortState<SortKey>>(null);
  const [suppliers, setSuppliers] = useState<SupplierRow[]>([]);
  const [formItems, setFormItems] = useState<FormItem[]>([]);
  const [quote, setQuote] = useState<QuoteBody | null>(null);
  const [unitCost, setUnitCost] = useState(0);

  useEffect(() => {
    setFormFacilityPk(facility.id);
    setNdc(undefined);
  }, [facility.id]);

  useEffect(() => {
    let cancelled = false;
    apiFetch("warehouse", "/suppliers")
      .then((body: { items: SupplierRow[] }) => {
        if (cancelled) return;
        const active = (body.items ?? []).filter((s) => s.active);
        setSuppliers(active);
        setSupplierId((prev) => prev ?? active[0]?.id);
      })
      .catch(() => {
        if (!cancelled) setSuppliers([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiFetch("inventory", `/items?facility_id=${formFacilityPk}&limit=200`)
      .then((body: { items: FormItem[] }) => {
        if (cancelled) return;
        setFormItems(body.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setFormItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, [formFacilityPk]);

  useEffect(() => {
    if (ndc && !formItems.some((i) => i.ndc === ndc)) setNdc(undefined);
  }, [formItems, ndc]);

  const supplier = suppliers.find((s) => s.id === supplierId);
  const selectedDrug = formItems.find((i) => i.ndc === ndc);

  useEffect(() => {
    if (!supplierId || !ndc || !formFacilityPk) {
      setQuote(null);
      setUnitCost(0);
      return;
    }
    let cancelled = false;
    Promise.all([
      apiFetch("warehouse", `/suppliers/${supplierId}/catalog?ndc=${encodeURIComponent(ndc)}`) as Promise<{ items: CatalogRow[] }>,
      apiFetch("warehouse", "/quote", {
        method: "POST",
        body: JSON.stringify({
          supplier_id: supplierId,
          facility_id: formFacilityPk,
          lines: [{ ndc, quantity }],
        }),
      }) as Promise<QuoteBody>,
    ])
      .then(([catalog, quoted]) => {
        if (cancelled) return;
        setUnitCost(catalog.items?.[0]?.unit_cost ?? 0);
        setQuote(quoted);
      })
      .catch(() => {
        if (cancelled) return;
        setQuote(null);
        setUnitCost(0);
      });
    return () => {
      cancelled = true;
    };
  }, [supplierId, ndc, quantity, formFacilityPk]);

  const drafts = orders.filter((o) => o.status === "draft");
  const filteredHistory = orders.filter((o) => (statusFilter === "all" ? true : o.status === statusFilter));
  const history = useMemo(() => {
    if (!sort) return filteredHistory;
    const dir = sort.direction === "asc" ? 1 : -1;
    return [...filteredHistory].sort((a, b) => dir * compareValues(sortValue(a, sort.key), sortValue(b, sort.key)));
  }, [filteredHistory, sort]);

  async function submitManual() {
    if (!selectedDrug || !supplierId) return;
    try {
      const order = await createOrder({
        facility_id: formFacilityPk,
        supplier_id: supplierId,
        status: "placed",
        source: "manual",
        lines: [{ ndc: selectedDrug.ndc, quantity }],
      });
      toast.success(`Order ${order.ref} placed with ${supplier?.name ?? "supplier"}.`, {
        description: `${quantity} of ${selectedDrug.name ?? selectedDrug.ndc} · ${money(quote?.total ?? 0)}`,
      });
      setNdc(undefined);
      setQuantity(100);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not place order.");
    }
  }

  async function placeDraft(order: OrderListItem) {
    try {
      await placeOrder(order.id);
      toast.success(`Order ${order.ref} placed with ${order.supplier.name ?? "supplier"}.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not place draft.");
    }
  }

  async function dropDraft(order: OrderListItem) {
    try {
      await discardDraft(order.id);
      toast(`Draft ${order.ref} discarded.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not discard draft.");
    }
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Purchase & Orders</h1>
        <p className="text-xs text-muted-foreground">
          Raise purchase orders, review AI-generated suggestions, and track delivery across the network.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          icon={Inbox}
          label="Drafts awaiting review"
          value={summary?.drafts_awaiting_review ?? drafts.length}
          hint="AI suggestions, all facilities"
          tone={(summary?.drafts_awaiting_review ?? drafts.length) > 0 ? "warning" : undefined}
        />
        <StatTile icon={Truck} label="In transit" value={summary?.in_transit ?? 0} hint="All facilities" tone="info" />
        <StatTile
          icon={PackageCheck}
          label="Delivered this month"
          value={summary?.delivered_this_month ?? 0}
          hint={summary?.timezone === "UTC" ? "Calendar month, UTC" : "This calendar month"}
        />
        <StatTile
          icon={Wallet}
          label="Committed spend"
          value={money(summary?.committed_spend.amount ?? 0)}
          hint={summary?.committed_spend.definition || "Placed + in transit"}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        {!canPlace ? (
          <Card className="gap-3 py-4">
            <CardHeader className="px-4">
              <CardTitle className="text-sm">New purchase order</CardTitle>
              <CardDescription className="text-xs">
                Placing an order commits spend and is restricted to Procurement. You can review and discard AI drafts.
              </CardDescription>
            </CardHeader>
          </Card>
        ) : (
        <Card className="gap-3 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">New purchase order</CardTitle>
            <CardDescription className="text-xs">Select the receiving pharmacy and a supplier to see live costing.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 px-4 text-xs">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <span className="text-muted-foreground">Receiving pharmacy</span>
                <Select value={String(formFacilityPk)} onValueChange={(v) => setFormFacilityPk(Number(v))}>
                  <SelectTrigger size="sm" className="h-8 w-full text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {operatedFacilities.map((f) => (
                        <SelectItem key={f.id} value={String(f.id)}>{f.name}</SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
                {formFacilityPk !== facility.id && (
                  <span className="text-[11px] text-amber-700 dark:text-amber-400">
                    On behalf of — your active site is {facility.name}.
                  </span>
                )}
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-muted-foreground">Supplier</span>
                <Select value={supplierId ? String(supplierId) : undefined} onValueChange={(v) => setSupplierId(Number(v))}>
                  <SelectTrigger size="sm" className="h-8 w-full text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {suppliers.map((s) => (
                        <SelectItem key={s.id} value={String(s.id)}>
                          {s.name} — {s.lead_time_days}d lead
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-muted-foreground">Drug</span>
                <Select value={ndc} onValueChange={setNdc}>
                  <SelectTrigger size="sm" className="h-8 w-full text-xs">
                    <SelectValue placeholder="Select a SKU" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {formItems.map((i) => (
                        <SelectItem key={i.ndc} value={i.ndc}>{i.name ?? i.ndc}</SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-muted-foreground">Quantity</span>
                <Input
                  type="number"
                  min={1}
                  value={quantity}
                  onChange={(e) => setQuantity(Math.max(1, Number(e.target.value)))}
                  className="h-8 text-right font-mono text-xs tabular-nums"
                />
              </div>
            </div>

            <Separator />

            <div className="flex flex-col gap-1.5 rounded-md bg-muted/40 p-3">
              <p className="mb-0.5 text-xs font-medium">Estimated cost</p>
              {!selectedDrug || !quote ? (
                <p className="py-2 text-center text-muted-foreground">Select a SKU to see unit cost, shipping, and expected delivery.</p>
              ) : (
                <>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Unit cost ({supplier?.name})</span>
                    <span className="font-mono tabular-nums">${unitCost.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Line total</span>
                    <span className="font-mono tabular-nums">{money(quote.subtotal)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Shipping</span>
                    <span className="font-mono tabular-nums">{money(quote.shipping)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Expected delivery (if ordered today)</span>
                    <span className="font-mono tabular-nums">
                      {quote.expected_delivery} ({quote.lead_time_days}d)
                    </span>
                  </div>
                  <Separator className="my-1" />
                  <div className="flex justify-between text-sm font-semibold">
                    <span>Estimated total</span>
                    <span className="font-mono tabular-nums">{money(quote.total)}</span>
                  </div>
                </>
              )}
            </div>
          </CardContent>
          <CardFooter className="px-4">
            <Button size="sm" className="h-8 w-full text-xs" disabled={!selectedDrug || !quote} onClick={() => void submitManual()}>
              <Truck data-icon="inline-start" />
              {selectedDrug && quote ? `Place order — ${money(quote.total)}` : "Select a SKU to continue"}
            </Button>
          </CardFooter>
        </Card>
        )}

        <Card className="gap-3 py-4">
          <CardHeader className="px-4">
            <CardTitle className="flex items-center gap-1.5 text-sm">
              <Sparkles className="size-4 text-primary" />
              AI suggestions awaiting review
            </CardTitle>
            <CardDescription className="text-xs">Draft orders raised from the forecast page and copilot.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 px-4 text-xs">
            {drafts.length === 0 ? (
              <div className="flex flex-col items-center gap-1.5 py-8 text-center">
                <Inbox className="size-6 text-muted-foreground/40" />
                <p className="text-xs font-medium">No drafts awaiting review</p>
                <p className="text-[11px] text-muted-foreground">
                  Accept an AI suggestion on Restock &amp; Forecasts and it lands here.
                </p>
              </div>
            ) : (
              drafts.map((o) => (
                <div key={o.id} className="flex flex-col gap-2 rounded-md border border-amber-500/30 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate font-medium">{o.primary_drug}</p>
                      <p className="truncate text-[11px] text-muted-foreground">
                        {o.facility.name} · {o.supplier.name}
                      </p>
                    </div>
                    <Badge variant="secondary" className="shrink-0 text-[10px] font-normal">AI Suggested</Badge>
                  </div>
                  <div className="flex justify-between font-mono tabular-nums">
                    <span className="text-muted-foreground">{o.quantity} units</span>
                    <span className="font-semibold">{money(o.total)}</span>
                  </div>
                  {o.note && <p className="text-[11px] text-muted-foreground">{o.note}</p>}
                  <div className="flex gap-2">
                    <Button variant="ghost" size="sm" className="h-7 flex-1 text-xs" onClick={() => void dropDraft(o)}>
                      <X data-icon="inline-start" />
                      Discard
                    </Button>
                    {canPlace && (
                      <Button size="sm" className="h-7 flex-1 text-xs" onClick={() => void placeDraft(o)}>
                        <CheckCircle2 data-icon="inline-start" />
                        Place
                      </Button>
                    )}
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="gap-3 py-4">
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 px-4">
          <div>
            <CardTitle className="flex items-center gap-1.5 text-sm">
              <ClipboardList className="size-4 text-muted-foreground" />
              Order history
            </CardTitle>
            <CardDescription className="text-xs">All purchase orders across the facility network.</CardDescription>
          </div>
          <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as typeof statusFilter)}>
            <SelectTrigger size="sm" className="h-8 w-40 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value="all">All statuses</SelectItem>
                {(Object.keys(STATUS_LABEL) as OrderStatus[]).map((s) => (
                  <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </CardHeader>
        <CardContent className="px-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-card">
                <TableRow>
                  <SortableHead sortKey="id" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))} className="pl-4">PO Ref</SortableHead>
                  <SortableHead sortKey="createdAt" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Created</SortableHead>
                  <SortableHead sortKey="facility" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Facility</SortableHead>
                  <SortableHead sortKey="supplier" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Supplier</SortableHead>
                  <SortableHead sortKey="drug" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Drug</SortableHead>
                  <SortableHead sortKey="qty" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Qty</SortableHead>
                  <SortableHead sortKey="total" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Total</SortableHead>
                  <SortableHead sortKey="source" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))}>Source</SortableHead>
                  <SortableHead sortKey="status" sort={sort} onSort={(k) => setSort(nextSortState(sort, k))} className="pr-4">Status</SortableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.map((o) => (
                  <TableRow key={o.id} className="text-xs">
                    <TableCell className="py-2 pl-4 font-mono text-[11px] tabular-nums">{o.ref}</TableCell>
                    <TableCell className="py-2 font-mono tabular-nums text-muted-foreground">{o.created_at}</TableCell>
                    <TableCell className="py-2">{o.facility.name}</TableCell>
                    <TableCell className="py-2 text-muted-foreground">{o.supplier.name}</TableCell>
                    <TableCell className="py-2 font-medium">{o.primary_drug}</TableCell>
                    <TableCell className="py-2 font-mono tabular-nums">{o.quantity}</TableCell>
                    <TableCell className="py-2 font-mono tabular-nums">{money(o.total)}</TableCell>
                    <TableCell className="py-2">
                      <Badge variant={o.source === "ai_suggestion" ? "secondary" : "outline"} className="text-[10px] font-normal">
                        {o.source === "ai_suggestion" ? "AI" : "Manual"}
                      </Badge>
                    </TableCell>
                    <TableCell className="py-2 pr-4">
                      <StatusBadge tone={STATUS_TONE[o.status]}>{STATUS_LABEL[o.status]}</StatusBadge>
                    </TableCell>
                  </TableRow>
                ))}
                {history.length === 0 && (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={9} className="py-10 text-center">
                      <ClipboardList className="mx-auto size-6 text-muted-foreground/50" />
                      <p className="mt-2 text-xs font-medium">No orders with this status</p>
                      <p className="text-[11px] text-muted-foreground">Clear the status filter to see the full history.</p>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
