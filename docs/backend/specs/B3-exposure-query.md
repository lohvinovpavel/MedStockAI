# B3 — Exposure query

**Service:** `inventory` · **Flows:** 4, 16 · **Status:** ✅ (wave 3) · **Depends on:** B2, B6, `ingest-shortages`

> The hourly FDA feed in `services/ingest/app/shortages.py` is still unverified. B3 joins
> the existing `shortage_event` table; the demo plants the three mock-aligned rows
> (Norepinephrine, Ceftriaxone, Heparin) so `uncovered` is a real claim.
>
> NDC resolution uses `demo_shelf` / `Drug.raw` / `consumption_daily` first and only
> calls live RxNorm for RxCUIs with no local pack list — a full NLM fan-out per
> `GET /exposure` is the same timeout B6 rule 5 forbids on import.

## Goal

`formulary × stock × shortage` — what `docs/services.md` once called `exposure-engine` and now
calls "a SQL query, executed inside `inventory`". It answers the question the KPI row is
actually asking: of the drugs we are committed to stocking, which are in a declared shortage,
and how covered are we?

## API

### `GET /api/inventory/exposure?facility_id=` — `inventory:read`

```json
{ "generated_at": "2026-08-15T10:00:00Z",
  "uncovered_rule": "below_par",
  "totals": { "formulary_skus": 322, "in_shortage": 7, "uncovered": 2 },
  "items": [
    { "rxcui": "1049640", "ndc": "0409-1782-01", "name": "Norepinephrine 4mg/4mL",
      "quantity": 12, "shortage_status": "Currently in Shortage",
      "shortage_source_id": "FDA-2026-0142", "days_of_supply": 3.5 }
  ]}
```

`days_of_supply` is attached from a 28-day trailing mean of `consumption_daily`
(E2's fallback). The canonical formula still lives in `prediction` `summarize()`;
B3 does not invent a placeholder when there is no history — the key is `null`.

## The query

```sql
SELECT f.rxcui, s.ndc, SUM(s.quantity) AS quantity, se.status, se.source_id
FROM formulary_item f
JOIN   rxnorm_ndc_map m  ON m.rxcui = f.rxcui       -- resolved via the shared rxnorm client
JOIN   stock_snapshot s  ON s.ndc   = m.ndc
LEFT JOIN shortage_event se ON se.ndc = s.ndc
WHERE (:facility_id IS NULL OR s.facility_id = :facility_id)
GROUP BY f.rxcui, s.ndc, se.status, se.source_id
```

`formulary_item` and `stock_snapshot` are tenant tables under RLS; `shortage_event` is
reference data with no `hospital_id`. That asymmetry is the whole point of the two-class split
in `docs/services.md` §1.1 — one shortage feed serves every tenant.

## Rules

1. Read-only. This endpoint never writes.
2. LEFT JOIN on shortages, not INNER: a formulary item with no shortage is still exposure data
   and is what `totals.formulary_skus` counts.
3. `uncovered` means in shortage **and** below par (B5). Without B5, define it as
   `quantity = 0` and report `uncovered_rule: "quantity_zero"` so the number on screen can
   always explain itself.
4. A formulary item with no stock row at all appears with `quantity: 0` — it is the most
   exposed case, not an absent one. Use a LEFT JOIN from formulary if the resolver returns no
   NDC match.
5. `ponytail: plain query, no materialized view. Materialize when a plan shows a seq scan that
   actually costs something.`

## Acceptance criteria

- [x] A formulary drug with an open `shortage_event` appears with its `source_id`.
- [x] A formulary drug with no stock row appears with `quantity: 0`, not missing.
- [x] A stocked drug absent from the formulary does **not** appear.
- [x] Two tenants with identical formularies see the same shortage rows and different quantities.
- [x] `totals` are computed in SQL, not by counting the serialized `items` array.

## Out of scope

Exposure trend over time, historical snapshots, cost-weighted exposure ranking.
