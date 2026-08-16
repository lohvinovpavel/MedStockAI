# G2 — Inter-facility transfer

**Service:** `warehouse` · **Flow:** 17 · **Status:** ❌ — a toast that changes nothing
**Depends on:** B1, B4, G1, H1 · **Scope:** `transfer:write`

## Goal

Flow 17 selects a surplus facility, a quantity, and renders a dispatch reference. No stock
moves, nothing is recorded, and the transfer never becomes anything. Make it a real movement
with a lifecycle — it is the cheaper answer to a shortage than a purchase order, and the
product's "redistribution network" claim rests on it.

## API

### `POST /api/warehouse/transfers` — `transfer:write`

```json
{ "from_facility_id": 4, "to_facility_id": 1, "ndc": "0409-1782-01",
  "quantity": 30, "shortage_id": "FDA-2026-0142", "note": null }
```

201:
```json
{ "id": 12, "ref": "TR-2026-0031", "status": "requested",
  "requested_at": "2026-08-15T14:22:00Z", "lines_reserved": [ { "lot": "NOR-25A", "quantity": 30 } ] }
```

### `PATCH /api/warehouse/transfers/{id}/status` — `transfer:write`

`{ "status": "dispatched" }` · `{ "status": "received" }` · `{ "status": "cancelled" }`

### `GET /api/warehouse/transfers?facility_id=&status=` — `inventory:read`

## Data model

```sql
CREATE TABLE transfer_request (
  id                bigserial PRIMARY KEY,
  ref               text NOT NULL,
  hospital_id       uuid NOT NULL,
  from_facility_id  bigint NOT NULL REFERENCES facility(id),
  to_facility_id    bigint NOT NULL REFERENCES facility(id),
  ndc               text NOT NULL,
  quantity          int NOT NULL CHECK (quantity > 0),
  status            text NOT NULL DEFAULT 'requested'
      CHECK (status IN ('requested','dispatched','received','cancelled')),
  shortage_id       text,
  note              text,
  requested_by      uuid,
  requested_at      timestamptz NOT NULL DEFAULT now(),
  dispatched_at     timestamptz,
  received_at       timestamptz,
  UNIQUE (hospital_id, ref),
  CHECK (from_facility_id <> to_facility_id)
);
```

## State machine and stock movement

```
requested ──dispatch──▶ dispatched ──receive──▶ received
    │                        │
    └──cancel────────────────┴──▶ cancelled
```

- **dispatch** debits the source `stock_batch` rows in FEFO order.
- **receive** credits the destination, preserving `lot` and `expiry_date` from the source
  batches. A transfer that resets expiry is a compliance defect, not a rounding detail.
- Both movements happen in the **same transaction as the status change**. Stock in flight is
  represented by the `dispatched` status, not by a moment where the units exist nowhere.
- **cancel** from `dispatched` credits the source back.

## Rules

1. Both facilities must be in the caller's tenant, and `to_facility` must be `operated = true`.
   A partner site can be a source only when the request carries `partner_source: true`.
2. Source must hold the quantity at dispatch time, checked with `SELECT … FOR UPDATE` on the
   batch rows. The surplus shown in G1 is a read from seconds ago; do not trust it.
3. `ref` from a sequence, formatted `TR-<year>-<seq:04d>`.
4. Cancelling a `received` transfer is 409. Reverse it with a new transfer in the other
   direction, so both movements stay in the audit trail.
5. Every transition writes `audit_log_entry` via H1.
6. A transfer that fully covers a shortage should surface on the F1 recommendation as
   `"covered_by_transfer": "TR-2026-0031"` rather than silently ordering the same units twice.

## Acceptance criteria

- [ ] Dispatch reduces source stock and does not yet increase destination stock.
- [ ] Receive increases destination stock with the source's lot and expiry preserved.
- [ ] Dispatching more than the source holds returns 422 and moves nothing.
- [ ] Cancelling a dispatched transfer restores source quantity exactly.
- [ ] `from_facility_id = to_facility_id` is rejected by the database.
- [ ] Two concurrent dispatches of the same batch cannot both succeed (row lock test).

## Out of scope

Courier/logistics integration, in-transit temperature logging, partial receipts, multi-SKU
transfers (one NDC per transfer for now), cross-tenant transfers.
