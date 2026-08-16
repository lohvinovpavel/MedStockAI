# F4 — Order history query

**Service:** `inventory` · **Flow:** 15 · **Status:** ❌ · **Depends on:** F3 · **Scope:** `order:read`

## Goal

The history table on `/orders` — PO ref, date, facility, supplier, drug, quantity, total,
source badge, status — filterable by status, and the KPI row above it. Reads only; F3 owns
every write.

## API

### `GET /api/inventory/orders?status=&facility_id=&supplier_id=&source=&from=&to=&limit=&offset=` — `order:read`

```json
{ "total": 42, "limit": 50, "offset": 0, "items": [
  { "id": 149, "ref": "PO-2026-0149", "created_at": "2026-08-15",
    "facility": { "id": 1, "name": "Central Hospital" },
    "supplier": { "id": 2, "name": "PharmaSource Global Ltd." },
    "status": "placed", "source": "ai_suggestion",
    "line_count": 1, "primary_drug": "Norepinephrine 4mg/4mL",
    "quantity": 150, "total": 1770.00, "expected_delivery": "2026-08-20" }
]}
```

`status` accepts repeats (`?status=draft&status=placed`). `primary_drug` and `quantity` are the
first line plus a count, so the table renders one row per order without N+1 fetches.

### `GET /api/inventory/orders/{id}` — `order:read`

Full order with all lines and the linked `review_decision` payload when AI-sourced — this is
what makes "why was this ordered" answerable from the history screen.

### `GET /api/inventory/orders/summary?facility_id=` — `order:read`

The KPI row:

```json
{ "drafts_awaiting_review": 3, "in_transit": 5,
  "delivered_this_month": 12,
  "committed_spend": { "amount": 18420.50, "currency": "USD",
                       "definition": "sum of line totals for placed and in_transit orders" } }
```

## Rules

1. `committed_spend` ships its own definition string. `docs/specs/UX-18` records that the tile
   currently means nothing on screen; a number whose definition is invisible is a number nobody
   can defend in a review.
2. `delivered_this_month` is calendar month in the **tenant's** timezone, not UTC. Store the
   tenant timezone or state UTC explicitly in the response — do not leave it ambiguous.
3. Totals are computed in SQL (`SUM(quantity * unit_cost) + shipping`), never by summing a
   serialized page.
4. Default sort `created_at DESC, id DESC` — the `id` tiebreak keeps pagination stable when two
   orders share a date.
5. `limit` default 50, max 200.
6. Drafts are included by default. They are the queue flow 13 works from; hiding them behind a
   filter would make the sidebar badge point at an empty screen.
7. Cross-tenant isolation is RLS. No `WHERE hospital_id`.

## Acceptance criteria

- [ ] Filtering by two statuses returns the union.
- [ ] `total` on a multi-line order equals the sum of its lines plus shipping.
- [ ] `drafts_awaiting_review` matches the sidebar badge count exactly.
- [ ] Page 2 contains no row from page 1 when orders share a `created_at`.
- [ ] One request renders the table — no per-order follow-up call for supplier or facility names.
- [ ] `committed_spend.definition` is present in every response.

## Out of scope

CSV export of order history (D3 covers the audit export), spend analytics by category,
supplier performance reporting, budget periods.
