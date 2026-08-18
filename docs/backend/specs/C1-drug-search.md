# C1 — Drug search (UC-1)

**Service:** `analogue` · **Flows:** 4, 19 · **Status:** ✅ implemented

`GET /drugs/search` in `services/analogue/app/main.py:109`, mounted both bare and under
`/api/analogue`. Queries live RxNorm from the service (never the browser), lifts
ingredient/SCDC hits to `SCD`/`SBD`, and returns candidates for **explicit** selection — a
single hit is still a list, and the client must not auto-pick. Gemini is not involved.

Sort is `in_formulary` desc, then RxNorm score. `in_formulary` is a LEFT JOIN to
`formulary_item` (B6 import / demo seed).

**No implementation work.** Verify:

- [x] `q` is bounded 1–120 characters and `limit` caps at 50.
- [x] After B6 imports a formulary, formulary hits sort first.
- [x] RxNorm being unreachable produces a clean error, not a 500 traceback.

Ukrainian trade names remain out of scope — RxNorm is US English, the same capstone feed choice
recorded in `docs/services.md` §7.
