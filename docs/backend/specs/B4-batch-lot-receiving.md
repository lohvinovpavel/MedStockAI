# B4 — Batch / lot receiving and FEFO

**Service:** `inventory` · **Flows:** 5, 15 · **Status:** ❌ · **Depends on:** B1

## Goal

Flow 5's Receive Batch dialog collects a lot number, quantity and expiry and throws all three
away (`docs/specs/UX-08`). It has nowhere to put them: `stock_snapshot` has a quantity and
nothing else. Every expiry, FEFO and waste-prevention claim in the product depends on this
table existing. This is a schema gap, not a UI gap.

## API

### `POST /api/inventory/batches` — `batch:write`

```json
{ "facility_id": 1, "ndc": "0093-0155-01", "lot": "AMX-24118-B",
  "expiry_date": "2027-03-31", "quantity": 120, "location_id": "shelf-a3" }
```

201 → the created batch plus the recomputed `stock_snapshot.quantity`.

### `GET /api/inventory/batches?ndc=&facility_id=&expiring_before=` — `inventory:read`

Ordered by `expiry_date ASC`. FEFO order is the default, not an option a caller can forget.

### `POST /api/inventory/batches/{id}/consume` — `batch:write`

`{ "quantity": 20, "reason": "dispense" }`. Decrements one batch. Which batch to consume is the
caller's decision; this endpoint only guarantees a batch cannot go below zero.

## Data model

```sql
CREATE TABLE stock_batch (
  id           bigserial PRIMARY KEY,
  hospital_id  uuid NOT NULL,
  facility_id  bigint NOT NULL REFERENCES facility(id),
  ndc          text NOT NULL,
  lot          text NOT NULL,
  expiry_date  date NOT NULL,
  quantity     int  NOT NULL CHECK (quantity >= 0),
  location_id  text NOT NULL DEFAULT '',
  received_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (hospital_id, facility_id, ndc, lot)
);
CREATE INDEX ix_stock_batch_fefo ON stock_batch (hospital_id, ndc, expiry_date);
```

`stock_snapshot.quantity` becomes a **derived rollup**:

```sql
CREATE FUNCTION refresh_stock_snapshot() RETURNS trigger AS $BODY$ ... $BODY$ LANGUAGE plpgsql;

CREATE TRIGGER stock_batch_rollup
  AFTER INSERT OR UPDATE OR DELETE ON stock_batch
  FOR EACH ROW EXECUTE FUNCTION refresh_stock_snapshot();
```

Same reasoning as `drug_certification.status`: derived, but stored so the hot read is one
indexed lookup instead of an aggregate per request.

## Rules

1. Receiving the same `(facility, ndc, lot)` twice **adds** to the existing row rather than
   creating a duplicate. That is what the UNIQUE constraint is for.
2. An `expiry_date` in the past is rejected with 422 on receive. Batches already in stock are
   allowed to expire — that is exactly what the expiry KPI counts.
3. Quantity is a plain integer in the SKU's own unit. No pack-size arithmetic anywhere in this
   feature.
4. Consumption never picks a batch implicitly. A caller wanting FEFO reads
   `GET /batches?ndc=` (already ordered) and consumes the head.
5. The batch write and the rollup are one transaction. A snapshot that disagrees with its
   batches is the exact bug this feature exists to prevent.
6. Every write lands in `audit_log_entry` through H1's trigger. Receiving stock is a regulated
   action, not bookkeeping.
7. The trigger must handle DELETE and the UPDATE-of-`ndc` case, not just INSERT — otherwise a
   corrected row silently leaves the old snapshot inflated.

## Acceptance criteria

- [ ] Receiving 120 then 40 of the same lot yields one row of 160 and a snapshot of 160.
- [ ] Receiving two lots of one NDC yields a snapshot equal to their sum.
- [ ] `GET /batches` returns soonest-expiring first with no sort parameter.
- [ ] Consuming more than a batch holds returns 422 and changes nothing.
- [ ] Deleting a batch row updates the snapshot.
- [ ] A past `expiry_date` on receive returns 422.

## Out of scope

DSCSA serialization, recalls by lot, quarantine states, moves between locations inside one
facility, cold-chain excursions.
