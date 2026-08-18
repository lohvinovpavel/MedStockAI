# F2 — Supplier and catalog

**Service:** `warehouse` · **Flow:** 14 · **Status:** ✅ (wave 4, `20260818_wave4`) · **Depends on:** B1 · **Scope:** `order:read` / admin for writes

## Goal

Flow 14's estimate panel — unit cost × quantity + shipping, lead time, expected delivery —
recomputes live when the supplier changes. Numbers live in `supplier` / `supplier_catalog`
(wave 4). F1 and F3 both read the same tables.

## API

### `GET /api/warehouse/suppliers?facility_id=` — `order:read`

```json
{ "items": [
  { "id": 2, "name": "PharmaSource Global Ltd.", "lead_time_days": 5,
    "reliability_pct": 97.4, "shipping_flat": 60.00, "currency": "USD" }
]}
```

### `GET /api/warehouse/suppliers/{id}/catalog?ndc=` — `order:read`

```json
{ "supplier_id": 2, "items": [
  { "ndc": "0409-1782-01", "unit_cost": 11.40, "pack_size": 10, "min_order_qty": 10 }
]}
```

### `POST /api/warehouse/quote` — `order:read`

The estimate itself, so the browser never does procurement arithmetic:

```json
{ "supplier_id": 2, "facility_id": 1, "lines": [ { "ndc": "…", "quantity": 150 } ] }
```
→
```json
{ "subtotal": 1710.0, "shipping": 60.0, "total": 1770.0,
  "lead_time_days": 5, "expected_delivery": "2026-08-20",
  "adjustments": [ { "ndc": "…", "requested": 145, "rounded_to": 150, "reason": "pack_size" } ] }
```

## Data model

```sql
CREATE TABLE supplier (
  id              bigserial PRIMARY KEY,
  hospital_id     uuid NOT NULL,
  name            text NOT NULL,
  lead_time_days  int  NOT NULL CHECK (lead_time_days >= 0),
  reliability_pct numeric(5,2) NOT NULL CHECK (reliability_pct BETWEEN 0 AND 100),
  shipping_flat   numeric(12,2) NOT NULL DEFAULT 0,
  currency        text NOT NULL DEFAULT 'USD',
  active          boolean NOT NULL DEFAULT true,
  UNIQUE (hospital_id, name)
);

CREATE TABLE supplier_catalog (
  id            bigserial PRIMARY KEY,
  supplier_id   bigint NOT NULL REFERENCES supplier(id) ON DELETE CASCADE,
  ndc           text NOT NULL,
  unit_cost     numeric(12,4) NOT NULL CHECK (unit_cost >= 0),
  pack_size     int NOT NULL DEFAULT 1 CHECK (pack_size >= 1),
  min_order_qty int NOT NULL DEFAULT 1,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (supplier_id, ndc)
);
```

Money is `numeric`, never `float`. The existing `drug_price.unit_price` is `text` because it is
kept as received from NADAC — that is a reference-data decision and does not apply here, where
the number is arithmetic.

## Rules

1. `POST /quote` is the single source of the estimate. The web client renders what it returns;
   it does not multiply anything itself (`docs/specs/UX-17` — the cost panel currently shows
   partial data because the arithmetic is split between layers).
2. Quantities round **up** to `pack_size` and never below `min_order_qty`. Every adjustment is
   reported in `adjustments`, because a total that silently differs from what the user typed is
   how procurement disputes start.
3. `expected_delivery` = today + `lead_time_days`, calendar days. Weekend/holiday calendars are
   out of scope; say so in the response with `"calendar": "calendar_days"`.
4. A supplier with no catalog row for an NDC cannot be quoted for it: 422 naming the NDC, not a
   zero-cost line.
5. `active = false` suppliers are readable (history references them) but rejected by `/quote`
   and by F3.
6. NADAC seeding is optional and one-way: `ingest-pricing` may populate `unit_cost` for
   suppliers flagged `seed_from_nadac`, never overwriting a manually set price.

## Acceptance criteria

- [x] Changing `supplier_id` in a quote changes both total and `expected_delivery`.
- [x] Requesting 145 with `pack_size` 10 quotes 150 and reports the adjustment.
- [x] An NDC absent from the catalog returns 422 rather than a $0 line.
- [x] Totals use `numeric` end to end; a test asserts no float rounding drift over 1,000 lines.
- [x] An inactive supplier is rejected by `/quote` but still resolves in order history.

## Out of scope

Contract tiers and volume discounts, multi-currency conversion, supplier SLAs and scorecards,
EDI/punch-out integration.
