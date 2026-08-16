# C3 — Analogue candidate graph

**Service:** `analogue` · **Flow:** 7 · **Status:** ✅ implemented

`GET /analogues/{rxcui}` in `services/analogue/app/main.py:251`. Walks RxNorm relations, filters
by indication/form/dose, prices via NADAC. Reads `rxnorm_edge` (unique on
`rxcui_from, rxcui_to, relationship`) and `drug_price` (unique on `ndc, effective_date`), both
populated by the `ingest-rxnorm` (weekly) and `ingest-pricing` (daily) CronJobs.

This produces the **candidate set**. C4 filters it; C5 will annotate it with local availability.
The separation matters: the model never generates candidates, it only removes them.

**No implementation work.** Verify:

- [ ] Stale reference data degrades gracefully — an empty `rxnorm_edge` yields an empty candidate
      list, not an exception.
- [ ] A candidate with no NADAC price still appears, priced null rather than dropped.
