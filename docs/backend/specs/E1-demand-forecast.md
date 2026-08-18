# E1 — Demand forecast

**Service:** `prediction` · **Flows:** 9, 12 · **Status:** ✅ issue #7 — `GET /forecast/{rxcui}` reads stored `forecast_point`; `POST /forecast/runs` writes a run (CronJob can wrap the same module later)
**Depends on:** B1, B2, B4 · **Scope:** `forecast:read`

## Goal

The forecast page used to advertise "Prophet v1.2", "XGBoost v0.9" and 94.2% confidence.
That is gone. `/forecasts` reads stored quantile rows from `forecast_point`.

**This is not an LLM feature.** The `# prediction — Mykhailo` slot in
`shared/medstock_shared/ai_tasks.py` is not where this goes. A language model cannot produce a
reproducible p10/p90, and E3 and H2 both depend on reproducibility.

> Implementation deviation: a GKE CronJob is still the target writer, but today
> `POST /api/prediction/forecast/runs` runs the engine synchronously for the caller's
> hospital. The GET path never fits a model (rule 5).

## Architecture

A **GKE CronJob**, nightly, writing rows; the service only reads them. Nobody needs a forecast
computed inside an HTTP request, and a request-time model is how a 30-second proxy timeout
becomes a production incident.

```
ingest-style CronJob (medstock-prediction, command: python -m app.forecast_job)
  → reads consumption history
  → fits one model per (facility_id, ndc) with enough history
  → writes forecast_point rows under a single run_id
GET /forecast/{rxcui} → one indexed read of the newest run
```

## API

### `GET /api/prediction/forecast/{rxcui}?facility_id=&horizon_days=30&surge_pct=100` — `forecast:read`

```json
{ "rxcui": "1049640", "facility_id": 1, "run_id": "0f2c…",
  "model_version": "seasonal_naive_quantile-1", "generated_at": "2026-08-15T02:00:00Z",
  "history": [ { "date": "2026-06-16", "quantity": 41 } ],
  "forecast": [ { "date": "2026-08-16", "p10": 33, "p50": 40, "p90": 52 } ],
  "depletion": { "date": "2026-08-29", "days": 14, "basis": "p50" } }
```

`surge_pct` is E3. `depletion` is E2's rule, surfaced here for the chart's `ReferenceLine`.

## Data model

```sql
CREATE TABLE forecast_point (
  id            bigserial PRIMARY KEY,
  hospital_id   uuid NOT NULL,
  facility_id   bigint NOT NULL REFERENCES facility(id),
  ndc           text NOT NULL,
  run_id        uuid NOT NULL,
  target_date   date NOT NULL,
  p10           numeric NOT NULL,
  p50           numeric NOT NULL,
  p90           numeric NOT NULL,
  model_version text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (hospital_id, facility_id, ndc, run_id, target_date),
  CHECK (p10 <= p50 AND p50 <= p90)
);
CREATE INDEX ix_forecast_lookup ON forecast_point (hospital_id, facility_id, ndc, run_id);
```

Runs are **immutable and kept**. A recommendation (F1) cites a `run_id`; deleting the run
destroys the evidence behind an order someone approved. Prune runs older than 90 days, not the
run a `review_decision` references.

## Model

Start at the bottom of the ladder and only climb if the error demands it:

1. **Seasonal naive with empirical quantiles** — median of the last 4 same-weekday values for
   p50, the 10th/90th percentiles of recent residuals for the band. No dependency, ~30 lines,
   and it is an honest interval because it is measured from actual error.
2. If that is materially wrong, `statsforecast` (`AutoETS`/`AutoTheta`) — a single small
   dependency, native quantile output.
3. Gradient boosting with **quantile loss** only if 1 and 2 both fail on real data. Not
   MSE-with-a-guessed-band, which is a guessed interval dressed as a quantile.

Whatever ships, `model_version` records it per row, so a chart can always say what drew it.

## Rules

1. An SKU with fewer than 21 days of history gets **no forecast row**. `GET /forecast` returns
   `"forecast": []` with `"reason": "insufficient_history"`. A confident line drawn from four
   data points is worse than an empty chart.
2. Consumption history comes from B4 `consume` events, falling back to day-over-day deltas of
   `stock_snapshot` until consumption is recorded. State which one in `basis`.
3. Receipts (B4 inserts) must not be read as negative demand.
4. The job is idempotent per day: re-running replaces that day's `run_id` rather than
   accumulating duplicate runs.
5. The endpoint never fits a model. If no run exists, say so — do not fall back to computing
   one inline.

## Acceptance criteria

- [ ] `CHECK (p10 <= p50 <= p90)` holds for every row the job writes.
- [ ] An SKU with 10 days of history produces no rows and a clear `reason`.
- [ ] Re-running the job twice on one day leaves one run per `(facility, ndc)`.
- [ ] Backtest: the p10–p90 band covers roughly 80% of held-out actuals — assert coverage
      between 0.65 and 0.95 on the demo dataset. This is the one test that makes the band mean
      anything.
- [ ] `GET /forecast` for an SKU with no run returns 200 and an empty array, not 500.
- [ ] The endpoint issues exactly one query.

## Out of scope

Cross-facility pooling, promotions/campaign effects, external epidemiological signals,
automatic retraining on drift, per-SKU model selection.
