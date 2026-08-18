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
import { useOrders } from "@/lib/orders-context";
import { useSession } from "@/lib/session";
import { can } from "@/lib/rbac";
import {
  facilityById,
  inventoryFor,
  isoPlusDays,
  operatedFacilities,
  orderTotal,
  supplierById,
  suppliers,
  today,
  type OrderStatus,
  type PurchaseOrder,
} from "@/lib/mock-data";

const STATUS_TONE: Record<OrderStatus, StatusTone> = {
  draft: "warning",
  placed: "surplus",
  in_transit: "surplus",
  delivered: "normal",
  cancelled: "critical",
};

const STATUS_LABEL: Record<OrderStatus, string> = {
  draft: "Draft",
  placed: "Placed",
  in_transit: "In transit",
  delivered: "Delivered",
  cancelled: "Cancelled",
};

function money(n: number) {
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

type SortKey = "id" | "createdAt" | "facility" | "supplier" | "drug" | "qty" | "total" | "source" | "status";

function sortValue(o: PurchaseOrder, key: SortKey): string | number {
  switch (key) {
    case "id": return o.id;
    case "createdAt": return new Date(o.createdAt).getTime();
    case "facility": return facilityById(o.facilityId).name;
    case "supplier": return supplierById(o.supplierId).name;
    case "drug": return o.drugName;
    case "qty": return o.quantity;
    case "total": return orderTotal(o);
    case "source": return o.source;
    case "status": return STATUS_LABEL[o.status];
  }
}

export default function OrdersPage() {
  const { user } = useSession();
  const canPlace = can(user?.role, "placeOrder");
  const { facilityId, facility } = useFacility();
  const { orders, addOrder, updateOrderStatus } = useOrders();

  // Order form — follows the facility you're currently operating as.
  const [formFacility, setFormFacility] = useState(facilityId);
  const [supplierId, setSupplierId] = useState(suppliers[0].id);
  const [drugId, setDrugId] = useState<string | undefined>();
  const [quantity, setQuantity] = useState(100);
  const [statusFilter, setStatusFilter] = useState<"all" | OrderStatus>("all");
  const [sort, setSort] = useState<SortState<SortKey>>(null);

  // Switching facility in the sidebar used to leave this form pointed at
  // the previous site with no indication the two disagreed. Following it
  // by default still allows an explicit override below — ordering on
  // behalf of another site is a real workflow — but the override no longer
  // survives a facility switch, same as the drug selection doesn't.
  useEffect(() => {
    setFormFacility(facilityId);
    setDrugId(undefined);
  }, [facilityId]);

  const formItems = useMemo(() => inventoryFor(formFacility), [formFacility]);
  const supplier = supplierById(supplierId);
  const selectedDrug = formItems.find((i) => i.id === drugId);

  // A SKU picked before a facility switch — sidebar-triggered or via the
  // form's own facility select — can outlive its facility's catalogue.
  // Clearing it here (rather than only where the switch happens) covers
  // both entry points from one place.
  useEffect(() => {
    if (drugId && !formItems.some((i) => i.id === drugId)) setDrugId(undefined);
  }, [formItems, drugId]);

  const unitCost = selectedDrug ? supplier.catalog[selectedDrug.id] ?? 0 : 0;
  const estimated = unitCost * quantity + supplier.shippingFlat;

  const drafts = orders.filter((o) => o.status === "draft");
  const filteredHistory = orders.filter((o) => (statusFilter === "all" ? true : o.status === statusFilter));
  const history = useMemo(() => {
    if (!sort) return filteredHistory;
    const dir = sort.direction === "asc" ? 1 : -1;
    return [...filteredHistory].sort((a, b) => dir * compareValues(sortValue(a, sort.key), sortValue(b, sort.key)));
  }, [filteredHistory, sort]);

  const thisMonth = orders.filter(
    (o) => o.status === "delivered" && new Date(o.createdAt).getUTCMonth() === today.getUTCMonth(),
  ).length;
  const committed = orders
    .filter((o) => o.status === "placed" || o.status === "in_transit")
    .reduce((sum, o) => sum + orderTotal(o), 0);

  function placeOrder() {
    if (!selectedDrug) return;
    const order = addOrder({
      facilityId: formFacility,
      supplierId,
      drugId: selectedDrug.id,
      drugName: selectedDrug.drugName,
      quantity,
      unit: selectedDrug.unit,
      unitCost,
      shipping: supplier.shippingFlat,
      status: "placed",
      source: "manual",
      expectedDelivery: isoPlusDays(supplier.leadTimeDays),
    });
    toast.success(`Order ${order.id} placed with ${supplier.name}.`, {
      description: `${quantity} ${selectedDrug.unit} of ${selectedDrug.drugName} · ${money(estimated)}`,
    });
    setDrugId(undefined);
    setQuantity(100);
  }

  function placeDraft(order: PurchaseOrder) {
    updateOrderStatus(order.id, "placed");
    toast.success(`Order ${order.id} placed with ${supplierById(order.supplierId).name}.`);
  }

  function discardDraft(order: PurchaseOrder) {
    updateOrderStatus(order.id, "cancelled");
    toast(`Draft ${order.id} discarded.`);
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
          value={drafts.length}
          hint="AI suggestions, all facilities"
          tone={drafts.length > 0 ? "warning" : undefined}
        />
        <StatTile icon={Truck} label="In transit" value={orders.filter((o) => o.status === "in_transit").length} hint="All facilities" tone="info" />
        <StatTile icon={PackageCheck} label="Delivered this month" value={thisMonth} hint="By order date, not delivery date" />
        <StatTile icon={Wallet} label="Committed spend" value={money(committed)} hint="Placed + in transit, all facilities" />
      </div>

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        {/* Committing spend is a Procurement Officer action (docs/rbac-matrix.md
            #13) — a pharmacist still reviews and discards AI drafts below, but
            can't raise or place a manual PO. */}
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
                <Select value={formFacility} onValueChange={setFormFacility}>
                  <SelectTrigger size="sm" className="h-8 w-full text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {operatedFacilities.map((f) => (
                        <SelectItem key={f.id} value={f.id}>{f.name}</SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
                {formFacility !== facilityId && (
                  <span className="text-[11px] text-amber-700 dark:text-amber-400">
                    On behalf of — your active site is {facility.name}.
                  </span>
                )}
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-muted-foreground">Supplier</span>
                <Select value={supplierId} onValueChange={setSupplierId}>
                  <SelectTrigger size="sm" className="h-8 w-full text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {suppliers.map((s) => (
                        <SelectItem key={s.id} value={s.id}>
                          {s.name} — {s.leadTimeDays}d lead
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-muted-foreground">Drug</span>
                <Select value={drugId} onValueChange={setDrugId}>
                  <SelectTrigger size="sm" className="h-8 w-full text-xs">
                    <SelectValue placeholder="Select a SKU" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {formItems.map((i) => (
                        <SelectItem key={i.id} value={i.id}>{i.drugName}</SelectItem>
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
              {!selectedDrug ? (
                <p className="py-2 text-center text-muted-foreground">Select a SKU to see unit cost, shipping, and expected delivery.</p>
              ) : (
                <>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Unit cost ({supplier.shortName})</span>
                    <span className="font-mono tabular-nums">${unitCost.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Line total</span>
                    <span className="font-mono tabular-nums">{money(unitCost * quantity)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Shipping</span>
                    <span className="font-mono tabular-nums">{money(supplier.shippingFlat)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Expected delivery (if ordered today)</span>
                    <span className="font-mono tabular-nums">
                      {isoPlusDays(supplier.leadTimeDays)} ({supplier.leadTimeDays}d)
                    </span>
                  </div>
                  <Separator className="my-1" />
                  <div className="flex justify-between text-sm font-semibold">
                    <span>Estimated total</span>
                    <span className="font-mono tabular-nums">{money(estimated)}</span>
                  </div>
                </>
              )}
            </div>
          </CardContent>
          <CardFooter className="px-4">
            <Button size="sm" className="h-8 w-full text-xs" disabled={!selectedDrug} onClick={placeOrder}>
              <Truck data-icon="inline-start" />
              {selectedDrug ? `Place order — ${money(estimated)}` : "Select a SKU to continue"}
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
            <CardDescription className="text-xs">Draft orders raised from the forecast page.</CardDescription>
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
                      <p className="truncate font-medium">{o.drugName}</p>
                      <p className="truncate text-[11px] text-muted-foreground">
                        {facilityById(o.facilityId).name} · {supplierById(o.supplierId).name}
                      </p>
                    </div>
                    <Badge variant="secondary" className="shrink-0 text-[10px] font-normal">AI Suggested</Badge>
                  </div>
                  <div className="flex justify-between font-mono tabular-nums">
                    <span className="text-muted-foreground">
                      {o.quantity} {o.unit}
                    </span>
                    <span className="font-semibold">{money(orderTotal(o))}</span>
                  </div>
                  {o.note && <p className="text-[11px] text-muted-foreground">{o.note}</p>}
                  <div className="flex gap-2">
                    <Button variant="ghost" size="sm" className="h-7 flex-1 text-xs" onClick={() => discardDraft(o)}>
                      <X data-icon="inline-start" />
                      Discard
                    </Button>
                    {canPlace && (
                      <Button size="sm" className="h-7 flex-1 text-xs" onClick={() => placeDraft(o)}>
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
                    <TableCell className="py-2 pl-4 font-mono text-[11px] tabular-nums">{o.id}</TableCell>
                    <TableCell className="py-2 font-mono tabular-nums text-muted-foreground">{o.createdAt}</TableCell>
                    <TableCell className="py-2">{facilityById(o.facilityId).name}</TableCell>
                    <TableCell className="py-2 text-muted-foreground">{supplierById(o.supplierId).name}</TableCell>
                    <TableCell className="py-2 font-medium">{o.drugName}</TableCell>
                    <TableCell className="py-2 font-mono tabular-nums">
                      {o.quantity} <span className="font-sans text-muted-foreground">{o.unit}</span>
                    </TableCell>
                    <TableCell className="py-2 font-mono tabular-nums">{money(orderTotal(o))}</TableCell>
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
