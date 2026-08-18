# E3 — Surge scenario

**Service:** `prediction` · **Flows:** 10, 11 · **Status:** ✅ `surge_pct` on `GET /forecast/{rxcui}` — `/forecasts` slider sends it
**Depends on:** E1, E2 · **Scope:** `forecast:read`

## Goal

Flow 10's slider (Standard 100% → Epidemic Surge 300%) used to recalculate the chart in the
browser. The number was real arithmetic but existed only in React state: it could not be cited
in a purchase order, reproduced from a screenshot, or audited. The multiplier now lives on
`GET /forecast/{rxcui}?surge_pct=` so the scenario is an answer the system stands behind.

Also closes `docs/specs/UX-04` — the simulator currently does not drive the PO suggestion.

## API

### `GET /api/prediction/forecast/{rxcui}?facility_id=&surge_pct=300` — `forecast:read`

Same response shape as E1, with:

```json
{ "surge_pct": 300, "scenario": "surge",
  "forecast": [ { "date": "2026-08-16", "p10": 99, "p50": 120, "p90": 156 } ],
  "depletion": { "date": "2026-08-18", "days": 3, "basis": "p50" },
  "baseline_depletion": { "date": "2026-08-29", "days": 14 } }
```

`baseline_depletion` is what makes the slider legible: "14 days normally, 3 under surge" is the
sentence, and both halves come from the same call.

### `GET /api/prediction/at-risk?surge_pct=` — `forecast:read`

The same multiplier applied across the facility, so "what breaks first under surge" is one
request rather than N.

## Rules

1. `surge_pct` is an integer, 100–300, default 100. Out of range → 422. 100 must return
   byte-identical output to omitting it.
2. Scaling multiplies **demand**, not stock: every quantile is scaled by `surge_pct / 100`.
   p10/p50/p90 scale together — a surge shifts the level, it does not change the model's
   relative uncertainty. Anything cleverer needs evidence this system does not have.
3. Nothing is written. This is a pure function over stored `forecast_point` rows, which is what
   makes it cheap and what makes it reproducible: same `run_id` + same `surge_pct` → same
   answer, forever.
4. The scenario must reach F1. A recommendation generated while a surge scenario is active
   records `surge_pct` in its payload, so an order for 3× the normal quantity carries the
   assumption that justified it. Without this the slider is theatre.
5. The scale factor is applied in SQL or in one helper — not duplicated between the forecast
   endpoint, the at-risk endpoint and the recommendation builder.
6. `scenario` is `"standard"` at 100 and `"surge"` above it. The UI's label copy
   (`docs/specs/UX-05`) derives from this field rather than re-deriving thresholds.

## Acceptance criteria

- [ ] `surge_pct=100` and no parameter produce identical bodies.
- [ ] `surge_pct=300` reduces `depletion.days` and never increases it.
- [ ] `surge_pct=0` and `surge_pct=301` both return 422.
- [ ] The endpoint issues no writes (assert on a read-only connection).
- [ ] A recommendation created at `surge_pct=200` stores that value in its payload.
- [ ] `baseline_depletion` matches a separate call at 100%.

## Out of scope

Named scenario presets stored server-side, per-SKU surge factors (an epidemic does not scale
every drug equally — real, but needs clinical input this system does not have), multi-week
ramp profiles.
