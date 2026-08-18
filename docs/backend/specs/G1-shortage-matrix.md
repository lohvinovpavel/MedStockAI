# G1 — Shortage matrix

**Service:** `inventory` · **Flow:** 16 · **Status:** ✅ — `GET /shortages` + `GET /shortages/{id}/coverage` (wave 4, `20260818_wave4`)
**Depends on:** B1, B2, B5, E2, `ingest-shortages` · **Scope:** `inventory:read`

## Goal

Flow 16 picks a shortage alert and shows coverage across every facility, colour-coded, with
surplus donors identified. It is the input to G2's transfer and the reason the product claims a
"live regional shortage-redistribution network". Coverage is derived from live `stock_snapshot`
plus trailing-mean days-of-supply (E2) — one derivation, not a second fixture.
(`docs/specs/UX-07` existed because two client-side datasets used to disagree.)

## API

### `GET /api/inventory/shortages?facility_id=` — `inventory:read`

Open alerts relevant to this tenant: a `shortage_event` whose NDC appears in our formulary or
our stock.

```json
{ "items": [
  { "id": "FDA-2026-0142", "ndc": "0409-1782-01", "rxcui": "1049640",
    "drug_name": "Norepinephrine 4mg/4mL", "status": "Currently in Shortage",
    "updated_at": "2026-08-12T00:00:00Z",
    "network": { "facilities_affected": 3, "surplus_facilities": 1, "worst_days_of_supply": 3.5 } }
]}
```

### `GET /api/inventory/shortages/{id}/coverage?facility_id=` — `inventory:read`

```json
{ "id": "FDA-2026-0142", "viewing_from": 1, "rows": [
  { "facility": { "id": 4, "name": "Regional Warehouse North", "type": "Warehouse",
                  "operated": true },
    "quantity": 1200, "days_of_supply": 88.0, "coverage": "surplus",
    "distance_km": 18.4, "is_current": false }
]}
```

## Coverage bands

Derived from E2 days-of-supply (trailing mean when no forecast run) — the
bands the shortage page paints. B2 `/items` status remains B5 par. Quantity=0
is stockout in both. Tests plant fixtures where the two agree (stockout and
warehouse surplus).

| Condition | `coverage` |
|---|---|
| `quantity = 0` | `stockout` |
| `days_of_supply <= 5` | `critical` |
| `days_of_supply >= 60` | `surplus` |
| otherwise | `normal` |

## Rules

1. One derivation of coverage, shared with B2's `status`. Two implementations is how the old
   client fixtures contradicted each other; the fix is a shared helper, not two carefully-matched copies.
2. `distance_km` is relative to `viewing_from`, not to a fixed origin. Reintroducing an
   absolute origin recreates the "Central Hospital · 0km away" bug.
3. Partner facilities (`operated = false`) appear in coverage — seeing a partner's surplus is
   the point — but are never proposed as a transfer source in G2 without an explicit flag.
4. Alerts are filtered to the tenant's relevance. `shortage_event` is global reference data;
   showing every FDA shortage in the country makes the screen useless.
5. Rows sort by `coverage` severity ascending then `distance_km` ascending, so the closest
   viable donor is visible without scrolling.
6. Read-only. G2 owns the write.

## Acceptance criteria

- [x] A tenant with no stock or formulary entry for a shortage NDC does not see that alert.
- [x] Coverage bands agree with `GET /api/inventory/items` status for the same SKU and facility.
- [x] `distance_km` for the viewing facility is 0 and it is flagged `is_current`.
- [x] A partner facility appears in coverage and is excluded from G2's source list.
- [x] `network.surplus_facilities` equals the count of rows with `coverage = "surplus"`.

## Out of scope

Predicted shortage propagation across the network, automatic transfer proposals, supplier-side
allocation data, historical shortage timelines.
