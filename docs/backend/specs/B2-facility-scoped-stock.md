# B2 — Facility-scoped stock read

**Service:** `inventory` · **Flows:** 3, 4, 7, 14 · **Depends on:** B1
**Status:** ✅ (wave 2). `GET /stock?rxcui=&facility_id=` and `GET /items` read `stock_snapshot`; the inventory page uses `InventoryProvider` → live API.

## Goal

The one read the whole dashboard sits on: given a clinical RxCUI — or nothing at all — return
the shelf rows for one facility. `GET /stock?rxcui=` joins RxNorm NDCs to `stock_snapshot`.
`GET /items` is the inventory table (flow 4); status is derived per B5, lot/expiry per B4.

## API

### `GET /api/inventory/stock?rxcui=&facility_id=` — `inventory:read`

RxCUI is the clinical id, NDC the shelf id. Resolution is RxCUI → NDCs (shared `rxnorm.py`
client) → `stock_snapshot` rows.

```json
{ "rxcui": "246461", "facility_id": 1, "rxnorm_degraded": false, "items": [
  { "ndc": "0093-0155-01", "name": "aspirin 100 MG Oral Tablet", "quantity": 340,
    "location_id": "shelf-a3", "in_formulary": true, "updated_at": "2026-08-14T09:00:00Z" }
]}
```

Empty stock is `"items": []` with 200 — **not** 404. "We hold none of this" is an answer.

### `GET /api/inventory/items?facility_id=&q=&status=&expiring_before=&limit=&offset=` — `inventory:read`

The inventory table itself (flow 4). `limit` defaults to 50, max 200. `q` matches name, INN or
ATC code. `status` is derived per B5, never stored: `stockout | critical | normal | surplus`.
`expiring_before` filters on the earliest batch expiry from B4.

## Rules

1. Omitting `facility_id` returns every facility in the tenant, so a network-wide KPI header
   is one call. The web client always sends it.
2. Tenant filtering is RLS (A4). No `WHERE hospital_id` in this service's SQL.
3. The RxNorm lookup is a live NLM call **from the service**, never from the browser. On
   upstream failure, degrade to matching `stock_snapshot.ndc` directly and set
   `rxnorm_degraded: true` — a stock read must not 500 because NLM is down.
4. `in_formulary` is a LEFT JOIN to `formulary_item` (B6). Dashboard SKUs resolve via the
   `demo_shelf` RxCUI map so live RxNorm is not required for the inventory badge.
5. Default sort is status severity descending, then name. The client should not have to
   re-sort to show the worst first.
6. `status` carries `par_defined: false` when no par row exists (B5) rather than guessing.

## Failure modes

| Condition | Response |
|---|---|
| Unknown `rxcui` | 200, empty `items` |
| `facility_id` belonging to another tenant | 404 — RLS returns no row; do not leak existence |
| NLM RxNorm timeout | 200 with `rxnorm_degraded: true` |
| `limit > 200` | 422 |

## Acceptance criteria

- [x] Two facilities holding different SKUs return disjoint lists for the same tenant.
- [x] A drug with zero on-hand returns 200 with an empty list, never 404.
- [x] With RxNorm stubbed to raise, the endpoint still answers 200 and flags degradation.
- [x] The 10-SKU demo dataset resolves in one query plus at most one RxNorm call.
- [x] Pagination is stable across pages (deterministic ORDER BY including a tiebreak on `id`).

## Out of scope

Write paths (B4 owns receiving), reservations/allocation, unit conversion between pack sizes.
