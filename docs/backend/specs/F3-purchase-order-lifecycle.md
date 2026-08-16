# F3 — Purchase order lifecycle

**Service:** `inventory` · **Flows:** 12, 13, 14, 15 · **Status:** ❌ — entirely `OrdersProvider` in browser memory
**Depends on:** B1, B4, F2, H1 · **Scope:** `order:write`

## Goal

Both order entry points — the AI draft from flow 12 and the manual form in flow 14 — converge
on one store today (`web/lib/orders-context.tsx`) that vanishes on refresh. Give it a table, a
state machine the server enforces, and a delivery step that actually moves stock.

## API

### `POST /api/inventory/orders` — `order:write`

```json
{ "facility_id": 1, "supplier_id": 2, "status": "draft",
  "source": "manual", "review_decision_id": null,
  "lines": [ { "ndc": "0409-1782-01", "quantity": 150 } ], "note": null }
```

201 → the created order with `ref`, priced through F2's quote at creation time.
`status` may be `draft` or `placed` on create — manual orders go straight to `placed`
(flow 14), AI suggestions land as `draft` (flow 12).

### `PATCH /api/inventory/orders/{id}/status` — `order:write`

`{ "status": "in_transit" }`. The only way status changes.

### `DELETE /api/inventory/orders/{id}` — `order:write`

Allowed on `draft` only; anything else must be `cancelled`, not deleted. This is flow 13's
Discard.

## Data model

```sql
CREATE TABLE purchase_order (
  id                 bigserial PRIMARY KEY,
  ref                text NOT NULL,                 -- PO-2026-0149
  hospital_id        uuid NOT NULL,
  facility_id        bigint NOT NULL REFERENCES facility(id),
  supplier_id        bigint NOT NULL REFERENCES supplier(id),
  status             text NOT NULL DEFAULT 'draft'
      CHECK (status IN ('draft','placed','in_transit','delivered','cancelled')),
  source             text NOT NULL CHECK (source IN ('ai_suggestion','manual')),
  review_decision_id bigint REFERENCES review_decision(id),
  shipping           numeric(12,2) NOT NULL DEFAULT 0,
  note               text,
  created_by         uuid,
  created_at         timestamptz NOT NULL DEFAULT now(),
  placed_at          timestamptz,
  expected_delivery  date,
  delivered_at       timestamptz,
  UNIQUE (hospital_id, ref)
);

CREATE TABLE purchase_order_line (
  id                bigserial PRIMARY KEY,
  purchase_order_id bigint NOT NULL REFERENCES purchase_order(id) ON DELETE CASCADE,
  ndc               text NOT NULL,
  quantity          int NOT NULL CHECK (quantity > 0),
  unit_cost         numeric(12,4) NOT NULL,     -- captured at creation, never re-joined
  UNIQUE (purchase_order_id, ndc)
);
```

`CHECK (source = 'manual' OR review_decision_id IS NOT NULL)` — an AI-sourced order without a
decision record is exactly the unauditable case this schema exists to prevent.

## State machine

```
draft ──place──▶ placed ──ship──▶ in_transit ──receive──▶ delivered
  │                 │                  │
  └──delete         └──cancel──────────┴──▶ cancelled
```

Enforced server-side in one transition table. `delivered` and `cancelled` are terminal.
Any other transition is 409 with the current status in the body.

## Rules

1. `ref` is allocated by a Postgres sequence, formatted `PO-<year>-<seq:04d>`. The mock's
   in-memory `nextRef = 149` counter is not portable across replicas.
2. `unit_cost` is copied from F2 at creation. An order total must stay reproducible after a
   price update — the same reasoning as `drug_certification.ruleset_version`.
3. **`delivered` writes stock.** The transition creates a `stock_batch` (B4) per line, in the
   same transaction. A delivered order that does not move inventory is the single most
   damaging inconsistency this system can have. Lot and expiry are required in the receive
   payload: `PATCH .../status {"status":"delivered","lines":[{"ndc":"…","lot":"…","expiry_date":"…"}]}`.
4. A `draft` may be edited (quantity, supplier, lines). A `placed` order may not — cancel and
   re-create.
5. Only `operated = true` facilities and `active = true` suppliers are valid on create.
6. Every create and every transition lands in `audit_log_entry` via the H1 trigger, with the
   before/after status.
7. `POST` is idempotent on an `Idempotency-Key` header if supplied — a double-clicked Place
   Order must not produce two orders.

## Acceptance criteria

- [ ] `draft → delivered` directly is rejected with 409.
- [ ] `delivered` creates one `stock_batch` per line and the snapshot reflects it.
- [ ] Deleting a `placed` order is rejected; cancelling it succeeds.
- [ ] Two concurrent creates produce two distinct refs (sequence, not read-modify-write).
- [ ] The same `Idempotency-Key` twice produces one order.
- [ ] An `ai_suggestion` order without `review_decision_id` is rejected by the DB constraint.
- [ ] Order total after a `supplier_catalog` price change is unchanged.

## Out of scope

Partial deliveries and backorders, order amendments after placing, supplier acknowledgement
callbacks, three-way invoice matching, multi-currency.
