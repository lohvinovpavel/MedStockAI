# E2 — Days of supply and at-risk list

**Service:** `prediction` · **Flows:** 4, 9, 16 · **Status:** ✅ `GET /at-risk` and `GET /days-of-supply` — `/forecasts` consumes them; inventory status is B5 par; B3 exposure and G1 shortage coverage attach trailing-mean `days_of_supply` when consumption exists
**Depends on:** B1, E1 · **Scope:** `forecast:read`

## Goal

"Days of supply is the core metric of the whole product" (`docs/services.md` §3). The formula
lives in `GET /at-risk` and `GET /days-of-supply`. `/forecasts` is the consumer that is live;
the inventory table uses B5 par status (wave 2). The shortage matrix still computes a local
mock days-of-supply.

## API

### `GET /api/prediction/at-risk?facility_id=&within_days=30` — `forecast:read`

```json
{ "facility_id": 1, "within_days": 30, "generated_at": "2026-08-15T10:00:00Z", "items": [
  { "ndc": "0409-1782-01", "rxcui": "1049640", "name": "Norepinephrine 4mg/4mL",
    "quantity": 12, "days_of_supply": 3.5, "depletion_date": "2026-08-18",
    "basis": "p50", "reorder_point": 20, "in_shortage": true, "run_id": "0f2c…" }
]}
```

Sorted by `days_of_supply` ascending — the worst first, always.

### `GET /api/prediction/days-of-supply?ndc=&facility_id=` — `forecast:read`

Single-SKU form for the inventory row and the analogue dialog.

## Definition

```
cumulative_demand(d) = Σ forecast p50 from tomorrow through day d
days_of_supply       = the smallest d where cumulative_demand(d) >= quantity_on_hand
depletion_date       = today + days_of_supply
```

- `basis: "p50"` is the headline. Also return `days_of_supply_p90` — the pessimistic case —
  because "14 days, or 9 if demand runs high" is the sentence a pharmacist actually needs.
- No forecast run → fall back to a 28-day trailing mean and set `basis: "trailing_mean"`.
- No history at all → `days_of_supply: null`, never `999` or `0`. Null renders as "unknown";
  a number renders as a claim.

## The query

Joins across four owners in one statement — the reason the system is one database:

```sql
SELECT s.ndc, s.quantity, f.p50, p.reorder_point, se.status
FROM stock_snapshot s
LEFT JOIN forecast_point f ON f.ndc = s.ndc AND f.facility_id = s.facility_id
                          AND f.run_id = :latest_run
LEFT JOIN par_level      p ON p.ndc = s.ndc AND p.facility_id = s.facility_id
LEFT JOIN shortage_event se ON se.ndc = s.ndc
WHERE s.facility_id = :facility_id
```

## Rules

1. `at_risk` means `days_of_supply <= within_days` **or** `quantity <= reorder_point`. Both,
   because a slow-moving critical SKU can sit below par for months without a depletion date
   inside the window.
2. `in_shortage` comes from `shortage_event`, which is reference data — no tenant filter.
3. Ties break on `quantity` ascending, then NDC, so the list is stable between polls.
4. This endpoint is the only place the days-of-supply formula lives. B2, B3, G1 and F1 call it
   or copy its result; they do not reimplement it.
5. `run_id` is echoed so a screenshot of the list can be traced to the exact forecast run.

## Acceptance criteria

- [ ] An SKU with 100 on hand and a flat forecast of 10/day reports 10 days.
- [ ] An SKU with no forecast run reports `basis: "trailing_mean"` and still returns a number.
- [ ] An SKU with no history at all reports `null`, and the UI shows "unknown".
- [ ] A below-par SKU with 60 days of supply still appears in `at-risk`.
- [ ] The list is sorted worst-first with no client-side re-sort.
- [ ] `days_of_supply_p90 <= days_of_supply` for every row.

## Out of scope

Lead-time-aware risk ("will it arrive before we run out" — that is F1's job), multi-facility
pooled coverage, expiry-adjusted supply.
