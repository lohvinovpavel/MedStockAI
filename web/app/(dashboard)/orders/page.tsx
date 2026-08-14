"use client";

import { useMemo, useState } from "react";
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusBadge, type StatusTone } from "@/components/dashboard/StatusBadge";
import { useFacility } from "@/lib/facility-context";
import { useOrders } from "@/lib/orders-context";
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
import { cn } from "@/lib/utils";

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

function KpiCard({ icon: Icon, label, value, tone }: { icon: typeof Inbox; label: string; value: string | number; tone?: "warning" | "info" }) {
  return (
    <Card className="gap-1 py-3">
      <CardContent className="flex items-center gap-3 px-4">
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-md",
            tone === "warning" && "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
            tone === "info" && "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-400",
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

export default function OrdersPage() {
  const { facilityId } = useFacility();
  const { orders, addOrder, updateOrderStatus } = useOrders();

  // Order form — defaults to the facility you're currently operating as.
  const [formFacility, setFormFacility] = useState(facilityId);
  const [supplierId, setSupplierId] = useState(suppliers[0].id);
  const [drugId, setDrugId] = useState<string | undefined>();
  const [quantity, setQuantity] = useState(100);
  const [statusFilter, setStatusFilter] = useState<"all" | OrderStatus>("all");

  const formItems = useMemo(() => inventoryFor(formFacility), [formFacility]);
  const supplier = supplierById(supplierId);
  const selectedDrug = formItems.find((i) => i.id === drugId);
  const unitCost = selectedDrug ? supplier.catalog[selectedDrug.id] ?? 0 : 0;
  const estimated = unitCost * quantity + supplier.shippingFlat;

  const drafts = orders.filter((o) => o.status === "draft");
  const history = orders.filter((o) => (statusFilter === "all" ? true : o.status === statusFilter));

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
        <KpiCard icon={Inbox} label="Drafts awaiting review" value={drafts.length} tone={drafts.length > 0 ? "warning" : undefined} />
        <KpiCard icon={Truck} label="In transit" value={orders.filter((o) => o.status === "in_transit").length} tone="info" />
        <KpiCard icon={PackageCheck} label="Delivered this month" value={thisMonth} />
        <KpiCard icon={Wallet} label="Committed spend" value={money(committed)} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[60%_40%]">
        <Card className="gap-3 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">New purchase order</CardTitle>
            <CardDescription className="text-xs">Select the receiving pharmacy and a supplier to see live costing.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 px-4 text-xs">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <span className="text-muted-foreground">Receiving pharmacy</span>
                <Select
                  value={formFacility}
                  onValueChange={(v) => {
                    setFormFacility(v);
                    setDrugId(undefined);
                  }}
                >
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
              <div className="flex justify-between">
                <span className="text-muted-foreground">
                  Unit cost {selectedDrug ? `(${supplier.name.split(" ")[0]})` : ""}
                </span>
                <span className="font-mono tabular-nums">{selectedDrug ? `$${unitCost.toFixed(2)}` : "—"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Line total</span>
                <span className="font-mono tabular-nums">{selectedDrug ? money(unitCost * quantity) : "—"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Shipping</span>
                <span className="font-mono tabular-nums">{money(supplier.shippingFlat)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Expected delivery</span>
                <span className="font-mono tabular-nums">
                  {isoPlusDays(supplier.leadTimeDays)} ({supplier.leadTimeDays}d)
                </span>
              </div>
              <Separator className="my-1" />
              <div className="flex justify-between text-sm font-semibold">
                <span>Estimated total</span>
                <span className="font-mono tabular-nums">{selectedDrug ? money(estimated) : "—"}</span>
              </div>
            </div>
          </CardContent>
          <CardFooter className="px-4">
            <Button size="sm" className="h-8 w-full text-xs" disabled={!selectedDrug} onClick={placeOrder}>
              <Truck data-icon="inline-start" />
              {selectedDrug ? `Place order — ${money(estimated)}` : "Select a SKU to continue"}
            </Button>
          </CardFooter>
        </Card>

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
                    <Button size="sm" className="h-7 flex-1 text-xs" onClick={() => placeDraft(o)}>
                      <CheckCircle2 data-icon="inline-start" />
                      Place
                    </Button>
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
                  <TableHead className="pl-4">PO Ref</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Facility</TableHead>
                  <TableHead>Supplier</TableHead>
                  <TableHead>Drug</TableHead>
                  <TableHead>Qty</TableHead>
                  <TableHead>Total</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead className="pr-4">Status</TableHead>
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
