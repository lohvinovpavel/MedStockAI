# B5 — Par level / reorder point

**Service:** `inventory` · **Flows:** 4, 12 · **Status:** ✅ (wave 2, migration `20260818_wave2_stock`) · **Depends on:** B1

## Goal

The inventory table paints rows red and the forecast page suggests a quantity. A par level
makes `critical` an objective claim and gives F1 a target to order up to.

## API

### `GET /api/inventory/par-levels?facility_id=&ndc=` — `inventory:read`

### `PUT /api/inventory/par-levels` — `par:write`

```json
{ "facility_id": 1, "ndc": "0093-0155-01", "reorder_point": 80, "target_qty": 240 }
```

Upsert semantics — PUT, not POST, because `(facility, ndc)` *is* the identity.

### `DELETE /api/inventory/par-levels/{id}` — `par:write`

## Data model

```sql
CREATE TABLE par_level (
  id            bigserial PRIMARY KEY,
  hospital_id   uuid NOT NULL,
  facility_id   bigint NOT NULL REFERENCES facility(id),
  ndc           text NOT NULL,
  reorder_point int NOT NULL CHECK (reorder_point >= 0),
  target_qty    int NOT NULL,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (hospital_id, facility_id, ndc),
  CHECK (target_qty > reorder_point)
);
```

## Rules

1. `target_qty > reorder_point` is a database constraint, not a validator. Ordering up to a
   level at or below the trigger point produces an immediate re-trigger loop.
2. Status derivation — used by B2 and B3, defined once, here:

   | Condition | Status |
   |---|---|
   | `quantity = 0` | `stockout` |
   | `quantity <= reorder_point` | `critical` |
   | `quantity >= target_qty * 2` | `surplus` |
   | otherwise | `normal` |

   With no par row for an SKU only `stockout` is claimable: return `normal` with
   `par_defined: false` so the UI stops implying a judgement the data cannot support.
3. Suggested order quantity is `target_qty - quantity_on_hand`, rounded up to the supplier's
   pack size when F2 knows one. F1 consumes this rule; it does not invent its own arithmetic.
4. Par levels are per facility. A clinic and a warehouse holding the same SKU have different
   correct answers, which is why `facility_id` is in the unique key.
5. Changing a par level is auditable (H1) — it moves what the system calls an emergency.

## Acceptance criteria

- [x] `PUT` twice on the same `(facility, ndc)` updates rather than duplicating.
- [x] `target_qty <= reorder_point` is rejected by the database even when the API validator is bypassed.
- [x] An SKU with no par row reports `par_defined: false` and never `critical`.
- [x] All four status bands are covered by one table-driven test.
- [x] The suggested-quantity helper is unit-tested independently of any endpoint.

## Out of scope

Deriving par from consumption history (E1's job once forecasts exist), seasonal par levels,
min/max by ward, safety-stock formulas with service-level targets.
