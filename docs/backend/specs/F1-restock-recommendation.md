# F1 — Restock recommendation

**Services:** `prediction` proposes · `inventory` owns the decision record
**Flows:** 12, 13 · **Status:** ❌ · **Depends on:** B5, E1, E2, F2, H1

## Goal

Flow 12's card is the product's headline moment: the system notices a depletion and proposes an
order a human accepts or declines. Today "Create Draft Order" writes to React state. Make the
proposal a stored, auditable decision — and make declining it as first-class as accepting it.

## API

### `GET /api/prediction/recommendations?facility_id=&surge_pct=` — `forecast:read`

```json
{ "items": [
  { "ndc": "0409-1782-01", "name": "Norepinephrine 4mg/4mL",
    "quantity": 150, "unit": "vials", "supplier_id": 2, "supplier_name": "PharmaSource Global",
    "unit_cost": 11.40, "shipping": 60.0, "estimated_total": 1770.0,
    "coverage_days": 30, "lead_time_days": 5,
    "rationale": { "days_of_supply": 3.5, "reorder_point": 20, "target_qty": 180,
                   "surge_pct": 100, "run_id": "0f2c…", "model_version": "…" } }
]}
```

Computed on read, not stored. A recommendation nobody acted on is not worth a row.

### `POST /api/inventory/recommendations` — `order:write`

Materialises one recommendation as a `review_decision` in `pending`. Returns its id. Called
when the card is rendered for review, or lazily on first action.

### `POST /api/inventory/recommendations/{id}/approve` — `recommendation:approve`

Creates the `purchase_order` (F3) in `draft` with `source = "ai_suggestion"` and
`review_decision_id` set. Returns the order.

### `POST /api/inventory/recommendations/{id}/reject` — `recommendation:approve`

`{ "reason": "stock arriving from transfer" }` → decision `rejected`, no order created.

## Data model

```sql
CREATE TABLE review_decision (
  id           bigserial PRIMARY KEY,
  hospital_id  uuid NOT NULL,
  facility_id  bigint NOT NULL REFERENCES facility(id),
  entity_type  text NOT NULL CHECK (entity_type IN ('restock_recommendation','analogue_substitution')),
  entity_ref   text NOT NULL,                       -- ndc, or rxcui pair for a substitution
  decision     text NOT NULL DEFAULT 'pending'
                 CHECK (decision IN ('pending','approved','rejected')),
  actor_id     uuid,
  reason       text,
  payload      jsonb NOT NULL,                      -- the recommendation exactly as shown
  created_at   timestamptz NOT NULL DEFAULT now(),
  decided_at   timestamptz
);
```

This is the table the audit trigger in `docs/services.md` §1.3 already assumes exists —
`audit_review_decision` fires on INSERT OR UPDATE of exactly this table.

## Rules

1. `payload` stores the recommendation **as displayed to the human**: quantity, supplier, unit
   cost, coverage days, and the full `rationale` block. Prices and forecasts change; what the
   approver saw must not.
2. Quantity comes from B5 (`target_qty - on_hand`), rounded up to F2's pack size. E1/E2 supply
   the urgency; they do not each invent an arithmetic.
3. Supplier selection: cheapest supplier whose `lead_time_days` beats `days_of_supply`; if none
   does, the fastest, with `"lead_time_risk": true` in the payload. Ordering something that
   arrives after the stockout should be visibly flagged, not silently chosen.
4. A rejection **must** be storable with a reason and must not create an order. Flow 12's
   Decline is currently local-only; the reason a pharmacist overrode the system is more
   valuable audit data than the acceptances.
5. Declining does not suppress the recommendation forever — it reappears on the next forecast
   run unless stock recovered. Suppression windows are a later feature; note it, do not build it.
6. `surge_pct` (E3) is recorded in the payload. An order for 3× normal must carry the
   assumption that produced it.
7. Approve and reject are idempotent: a second call on a decided recommendation returns 409
   with the existing decision, and never creates a second order.

## Acceptance criteria

- [ ] Approving creates exactly one `purchase_order` in `draft`, linked by `review_decision_id`.
- [ ] Rejecting creates no order and stores the reason.
- [ ] Double-approve returns 409 and leaves one order.
- [ ] The stored `payload` still shows the original unit cost after `supplier_catalog` changes.
- [ ] A recommendation for a facility with no par row is not generated (nothing to order up to).
- [ ] Both transitions produce an `audit_log_entry` row without any application code calling `audit()`.

## Out of scope

Multi-line recommendations (one SKU per recommendation for now), budget approval thresholds,
supplier contract terms, auto-approval above a confidence level — a human decides, always.
