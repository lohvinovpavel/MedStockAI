# C5 — Local availability overlay on analogues

**Service:** `analogue` · **Flow:** 7 · **Status:** ❌ · **Depends on:** B1, B2, C3, C4

## Goal

`GET /analogues/{rxcui}` already returns a ranked, AI-filtered, citation-checked list. It does
not say whether we actually have any of them. Flow 7's dialog shows "340 boxes here" versus
"Not stocked here" plus the nearest facility that does hold it — the single piece of
information that turns a therapeutic list into a decision.

## API

### `GET /api/analogue/analogues/{rxcui}?facility_id=` — `drug:search` + `inventory:read`

`facility_id` is optional. Without it the response is exactly what it is today, so no existing
caller breaks.

```json
{ "rxcui": "1049640", "ai_filtered": true, "items": [
  { "rxcui": "861467", "name": "Phenylephrine 10 mg/mL", "equivalence": "therapeutic",
    "source": "RxNorm", "match_score": 96, "citation": "…verbatim sentence…",
    "availability": {
      "facility_id": 1, "quantity": 340, "unit": "vials",
      "nearest_with_stock": { "facility_id": 4, "name": "Regional Warehouse North",
                              "quantity": 1200, "distance_km": 18.4 }
    }}
]}
```

`availability.quantity: 0` with a non-null `nearest_with_stock` is the "not stocked here, but
18 km away" case that feeds flow 17.

## Implementation

1. C3/C4 produce the candidate rxcui list, unchanged.
2. Resolve each candidate rxcui → NDCs via the shared RxNorm client (already in the request
   path for packages).
3. One grouped query over `stock_snapshot` for **all** candidate NDCs at once — not one query
   per candidate.
4. `nearest_with_stock` is computed with B1's haversine over facilities that have
   `quantity > 0`, excluding the requesting facility and excluding `operated = false` sites
   only if the caller passes `?operated_only=true`. Partner stock is visible by design; it is
   what the shortage matrix is for.

## Rules

1. Availability is an **overlay**, never a filter. An analogue with zero stock everywhere is
   still clinically relevant and still ranked — it just cannot be dispensed today.
2. Ranking order does not change. Match score decides order; availability decides colour.
   Mixing the two would make a mediocre substitute outrank a good one because a shelf happened
   to be full.
3. Distance is relative to `facility_id`, not to Central. This is the exact bug already fixed
   on the mock side (`Math.abs(f.distanceKm - facility.distanceKm)`); do not reintroduce it
   server-side.
4. If the stock query fails, return the analogue list with `availability: null` and a
   `stock_degraded: true` flag rather than failing the whole request — same degradation
   principle as `ask_ai` failing back to the unfiltered list.
5. `analogue` reads `stock_snapshot` directly. That is intentional (`docs/services.md` §0: one
   database, seven deploy units), not a boundary violation to be refactored into an HTTP call.

## Acceptance criteria

- [ ] Without `facility_id` the response is byte-identical to today's.
- [ ] Switching `facility_id` flips a candidate between stocked and not-stocked.
- [ ] `nearest_with_stock` never names the requesting facility.
- [ ] Candidate count N produces one stock query, not N.
- [ ] With `stock_snapshot` unavailable, the endpoint still returns ranked analogues.

## Out of scope

Reserving stock from the dialog, cross-tenant availability, pricing comparison between
analogues (C3 already carries NADAC), pack-size normalisation.
