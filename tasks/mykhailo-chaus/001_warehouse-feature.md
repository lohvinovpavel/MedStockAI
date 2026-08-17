# 001 — Warehouse service: structure, conditions & synthetic 3-year demo data (issue #8)

**Status:** IN_PROGRESS
**Created:** 2026-08-17 · **Updated:** 2026-08-17

> This file is the plan **and** the session-recovery point for this work. Keep it
> current in-flight: after each meaningful unit of work, tick the checklist item,
> add a dated Worklog line naming the concrete artifact, and refresh the Resume
> section. A fresh session given only this file must be able to continue without
> asking "what were we doing?"

## Objective

Implement the warehouse service (GitHub issue #8 — "Warehouse — service to track warehouse structure, physical indications"): facility/location registry per spec B1, storage-condition monitoring with excursion detection, and a deterministic synthetic-data pipeline producing 3 years of daily drug consumption plus 90 days of hourly location telemetry — the dataset issue #7 (prediction) will forecast from — all visualized on a new dashboard page. Delivered as **one PR** on `feature/warehouse`.

## Context

- Issue #8 and #7 are title-only; the real specs live in the repo:
  - `docs/backend/specs/B1-facility-registry.md` — `facility` table DDL, `GET /facilities`, `/locations`, `stock_snapshot.location_id` text→FK migration, 6 seeded sites.
  - `docs/backend/specs/E1-demand-forecast.md` — what prediction (#7) will consume; assumes consumption history that has **no table today**.
  - `docs/demo-data.md` — seeding doctrine: "Reference data is real. Tenant data is synthetic." `DEMO_SEED=42`, demo tenant `demo-hospital-001`, seeder spec'd as `services/ingest/app/seed_demo.py` (doesn't exist), seed refuses unless `ENVIRONMENT=demo`. Its "90 days of usage history" line is amended to 3 years by this task.
- `services/warehouse/` is a `/healthz`-only skeleton; `deploy/k8s/warehouse.yaml` + ingress `/api/warehouse` already exist.
- Prior art to copy: `services/inventory/app/main.py` (router + `require()` + `session_scope` idiom), `scripts/seed_stock.py` (curated real RxCUIs, `RNG_SEED=42`, idempotent upserts), `services/ingest/app/*.py` (`python -m app.<name>` entrypoints, `ON CONFLICT DO UPDATE`).
- Web: `recharts` installed, chart primitive at `web/components/ui/chart.tsx`; dashboard pages run on `web/lib/mock-data.ts` (its `facilities[]` matches B1's six sites) — left untouched.
- Branch note: `.gitignore` has an unresolved UU merge conflict; working-tree file is already the correct union — just `git add .gitignore` before first commit. `web/package-lock.json` also has stray modifications to review.

## Acceptance Criteria

- [ ] Migrations create `facility`, `location` (with `kind`), `consumption_daily`, `location_condition`; `stock_snapshot.location_id` becomes an FK. All tables in `shared/medstock_shared/models.py` first; `alembic upgrade head` clean from current head `20260815_patient`.
- [ ] `data/demo/drugs.csv` committed: ~100 real drugs (RxCUI + NDC resolved once via RxNav/openFDA), each with cohort tag and storage-class columns (`storage_min_c`, `storage_max_c`, `humidity_max_pct`); refrigerated contingent present (insulins, vaccines).
- [ ] Generator `python -m app.gen_demo` (ingest image) is deterministic under `DEMO_SEED=42` and writes committed `data/demo/*.csv.gz`: 3y × daily × 100 drugs × 6 facilities consumption; 90d × hourly temp/humidity per location with planted excursions (fridge failure, warehouse-north summer drift, humidity breach) and one misplaced refrigerated drug.
- [ ] Seeder `services/ingest/app/seed_demo.py` loads artifacts idempotently, refuses unless `ENVIRONMENT=demo`, seeds one tenant (`demo-hospital-001`) with B1's six facilities; `stock_snapshot` quantities equal the balance implied by consumption-history tails.
- [ ] Warehouse API behind `require("warehouse:read")`: `GET /facilities`, `GET /facilities/{id}`, `GET /locations?facility_id=`, `GET /stock?facility_id=`, `GET /consumption?ndc=&facility_id=&from=&to=`, `GET /locations/{id}/conditions?from=&to=`, `GET /excursions?facility_id=` (computed on read: readings × stock placement × storage requirements → violation records naming affected stock).
- [ ] New `web/app/(dashboard)/warehouse` page against the real API: facility selector, locations/stock table, per-drug 3-year consumption chart with visible seasonality, conditions chart with requirement bands, excursion alert list. `npm run build` passes.
- [ ] All four test layers pass via the per-service CI flow (`uv sync --package medstock-warehouse` / `medstock-ingest`; `uv run --no-sync pytest`):
  1. determinism (same seed → byte-identical artifacts, hash check);
  2. statistical contract (winter > summer for seasonal cohort; weekend dip; trending year-3 mean > year-1; stockout windows recorded as zeros; planted excursions → exactly the expected violation records from `/excursions`);
  3. consistency (history tail balance == seeded `stock_snapshot.quantity` per drug×facility);
  4. API (401/403 via `require`, RLS invisibility → 404, date-window edge cases).
- [ ] Documentation updated to match the implemented decisions: `docs/demo-data.md` (90 days → 3 years, artifacts, gen_demo/seed_demo), `docs/services.md` warehouse row (conditions/excursions responsibility, endpoint list), `docs/backend/db-schema.md` (new tables, built-status matrix), `docs/backend/backend-features.md` status column, B1 spec marked implemented, and a note on the consumption_daily + location_condition design (cohorts, planted scenarios, storage classes) so #7 has a written contract.
- [ ] One PR from `feature/warehouse` to `main`.

## Design Decisions

(Settled in a grilling session, 2026-08-17.)

- **Scope**: generator is a deliverable of #8 (user decision) — data must exist to visualize warehouse state. Suppliers (F2), transfers (G2), connector admin endpoints: **out of scope**, separate issues later.
- **Generation method**: hand-rolled numpy — base level × weekly profile (weekend dip) × annual seasonal curve × trend + noise + demand spikes; simple (s,S) reorder simulation produces stockout windows recorded as zeros (censoring) for 2–3 designated drugs. Rejected: Nike `timeseries-generator` (abandoned, pandas<2 pins, won't install on py312); Synthea (heavyweight Java step, module-driven volumes, fabricates the warehouse layer anyway — real RxCUIs come from our own curated list instead); `stockpyl` (the reorder sim is ~30 lines, not worth a dependency).
- **Artifact storage**: deterministic generation **and** committed artifacts (`data/demo/*.csv.gz`, few MB) — user wants no regeneration on every machine/CI run. A regenerate-and-diff test keeps the committed files honest against the generator code.
- **Consumption schema**: pre-aggregated **`consumption_daily`** `(hospital_id, facility_id, ndc, rxcui, date, qty_consumed, stockout bool)`, uq `(hospital_id, facility_id, ndc, date)`, standard RLS tenant shape. Rejected event-ledger for v1; when B4 consume events arrive, the ledger becomes the source and this table the derived rollup — survives unchanged. Both `ndc` and `rxcui` denormalized (consumption is NDC-grain; E1 forecasts by RxCUI).
- **Panel shape**: 3 years, daily, per drug × facility, ~100 real drugs, **one** demo hospital (user decision — no second tenant), B1's six facilities (`central`, `riverside`, `westend`, `warehouse-north` + 2 non-operated partners) — spec'd in B1 and mirrored by the frontend mock.
- **Planted signals** (the contract with #7): weekly + annual seasonality + trend + noise + spikes, drug cohorts (seasonal / flat / trending / stockout-censored); stockout censoring deliberately included so #7 can demo "a zero during a stockout isn't zero demand".
- **Conditions feature** (the "physical indications" of the issue title; user-added scope): `location.kind` (room / fridge / cold-room…), ~3–4 locations per facility; hourly temp+humidity for last 90 days; storage requirements as class-level columns on drugs.csv (refrigerated 2–8 °C, CRT 15–25 °C, freezer −25…−15 °C) — rejected parsing openFDA SPL storage text (fragile, reduces to the same classes; ingest can overwrite later). Stock placement respects storage class, with one deliberately misplaced drug.
- **Excursions**: computed on read (`GET /excursions`), no stored alert table, no background job — join readings × placement × requirements.
- **Web**: new page only; existing mock-data contexts untouched (migrating them is a later issue).
- **Delivery**: **one PR** (user decision, revised from the 3-PR suggestion).

## Implementation Checklist

- [x] 1. Resolve branch hygiene: `git add .gitignore` (union already in working tree); review/settle `web/package-lock.json` modifications.
- [x] 2. Models + migration: `facility`, `storage_location`, `consumption_daily`, `location_condition` in `shared/medstock_shared/models.py`; revision `20260817_warehouse` (down_rev `20260815_patient`); per B1 `stock_snapshot` gains nullable `facility_id` FK and `location_id` stays as intra-facility shelf code; storage-requirement columns added to `drug`; `facility:read` perm added to all roles in `shared/medstock_shared/auth.py`.
- [x] 3. Build `data/demo/drugs.csv`: 100 real drugs resolved via RxNav by `scripts/build_demo_drugs.py` (0 failures; branded SBD fallback when generic SCD has no marketed NDC). Cohorts 66 flat / 15 winter / 9 summer / 8 trending_up / 2 trending_down; storage 84 crt / 14 refrigerated / 2 freezer; stockout-prone: amoxicillin 500, Ventolin HFA, Ozempic.
- [x] 4. Generator `services/ingest/app/gen_demo.py` (+ shared `demo_layout.py`): 434,016 consumption rows / 535 stock rows / 21,600 condition readings in ~1.5 s; deterministic gzip (mtime=0), byte-identical across runs; artifacts in `data/demo/`. Bulk-hall summer coupling tuned so breaches concentrate in the planted heatwave (westend misplaced drug 2160 h, bulk-room 102 h, central fridge 7 h).
- [x] 5. Seeder `services/ingest/app/seed_demo.py`: ENVIRONMENT=demo guard, upserts hospital/facilities/locations/drugs/formulary/stock, delete-and-reload for the two bulk series; ~15 s, idempotent (verified by double-run).
- [x] 6. Warehouse service `services/warehouse/app/main.py`: facilities (+operated filter, haversine `?from=` distance), locations, stock (drug join), consumption (date window), conditions, excursions (grouped per location×drug with affected quantity). Perms: `facility:read` for registry/conditions/excursions, `inventory:read` for stock/consumption. Mounted at `""` and `/api/warehouse`.
- [x] 7. Tests green: 33 ingest (determinism regenerate-and-diff, statistical contract incl. heatwave dominance, stock-vs-history-tail consistency, seed guard) + 12 warehouse (auth 401/403, 404s, distance, date windows, planted excursion detected exactly once). RLS cross-tenant test deferred — policies are a repo-wide open item (docs/services.md §8), noted in test docstring.
- [x] 8. Web `(dashboard)/warehouse` page: facility selector, excursion alert callout (grouped per location with affected-stock table), consumption chart (90d/1y/3y ranges, weekly bucketing, stockout windows as red reference areas), separate temperature + humidity charts with strictest-requirement lines, stock-by-location table. Legacy stub `(legacy)/warehouse` removed (route collision). SideNav entry + dev rewrite :8004 added. `npm run build` passes. All endpoints verified through the Next proxy with a minted dev JWT; visual eyeball pending (Chrome extension not connected) — stack left running at localhost:3002/warehouse.
- [x] 9. Docs sweep done: `docs/demo-data.md` (tenant tables, gen/seed split, 3 years, new §6a planted-signal contract for #7), `docs/services.md` warehouse row (endpoints + conditions), `docs/backend/db-schema.md` (registry rows + build-order note), `docs/backend/backend-features.md` (B1 ✅ + new B7 row, B2 uq note), B1 spec status ✅ with deviations block.
- [x] 10. Verified: migration down/up (downgrade deletes facility-scoped stock rows before restoring the narrow key — destructive by necessity, commented), reseed, full 8-service test matrix (209 passed), ruff clean, `npm run build` clean. Committed as `0b03be0`, pushed over HTTPS (SSH key maps to a no-push account), **PR #29**: https://github.com/lohvinovpavel/MedStockAI/pull/29.

## Testing Plan

Per-service flow exactly as CI runs it:
```bash
uv sync --package medstock-warehouse && uv run --no-sync pytest services/warehouse/tests -q
uv sync --package medstock-ingest    && uv run --no-sync pytest services/ingest/tests -q
cd web && npm run build
```
Layers: determinism hash · statistical properties · snapshot/history consistency · API auth/RLS/edges · planted-excursion detection. Local stack per `.cursor/hooks` convention (postgres container, inventory :8001, analogue :8002, web :3000) for manual demo check.

## Blockers / Dependencies

- RxNav/openFDA reachable once for step 3 (result committed; no runtime dependency).
- No RLS policies exist yet in migrations repo-wide (`docs/services.md` §8 open item) — new tenant tables follow the same convention as existing ones (shape ready, policies pending globally). Not a blocker.

## Open Questions

None — all settled in the grilling session (see Design Decisions).

## Resume

### In-flight artifacts
- All work committed as `0b03be0` on `feature/warehouse`, pushed; **PR #29** open against `main` (https://github.com/lohvinovpavel/MedStockAI/pull/29). Working tree clean apart from task-file updates.
- Local dev stack left running: warehouse API on :8004 (dev JWT keys + token in the session scratchpad), `npm run dev` on :3002 (:3000 held by the cursor stop-hook's older instance). Local Postgres `medstock-postgres` is migrated to head and demo-seeded.
- Push note: origin's SSH key maps to `mykhailochaus-GLO` (no push); push via HTTPS with gh (`MChaus`) credentials.

### First actions on resume
1. Check PR #29 CI (per-service matrix) — `gh pr checks 29 --repo lohvinovpavel/MedStockAI`; fix anything red.
2. Visual pass of `localhost:3002/warehouse` (Chrome extension was unavailable this session) — needs a `medstock_token` cookie; mint per `services/auth/README.md` local-dev recipe.
3. On merge: mark this task DONE; issue #7 (prediction) builds on `consumption_daily` + the §6a contract in `docs/demo-data.md`.
- Anti-patterns: don't regenerate artifacts unless the generator changed (regen changes committed bytes; the determinism test enforces agreement); don't re-add the `(legacy)/warehouse` stub (route collision).

## Worklog

- 2026-08-17: Task created after grilling session (4 rounds); plan settled with user.
- 2026-08-17: Docs-sweep requirement added at user's request (AC + step 9 broadened).
- 2026-08-17: Plan approved by user. Status → IN_PROGRESS.
- 2026-08-17: Steps 1–2 done. `.gitignore` staged; incidental `web/package-lock.json` churn (npm libc-field noise) restored to HEAD. Migration `migrations/versions/20260817_warehouse.py` up/down verified against local Postgres; ruff clean. Design deltas vs. plan, driven by B1 spec: perm named `facility:read` (not `warehouse:read`); `stock_snapshot.location_id` NOT retyped to FK — instead `facility_id` FK added and `location_id` remains the shelf code matching `storage_location.code`; storage requirements live on global `drug` table (excursion query needs them in DB, not just CSV); facility seed rows go in seed_demo (deviates from B1 rule 5 "ship as migration" — demo-data.md's ENVIRONMENT=demo guard wins).
- 2026-08-17: Steps 3–10 done — drugs.csv (100/100 RxNav-resolved), gen_demo/seed_demo + committed artifacts, warehouse API, 45 new tests (209 total matrix green), dashboard page (`npm run build` clean), docs sweep, commit `0b03be0`, **PR #29** opened. Further design deltas: stock natural key widened to include `facility_id` (every facility has a "fridge-1"); consumption chart anchored to the data's fixed END_DATE 2026-08-14, not the wall clock; migration downgrade deletes facility-scoped stock rows before restoring the narrow key (documented destructive); bulk-hall summer coupling tuned so excursions tell a three-tier story (2160 h misplaced drug / 102 h heatwave / 7 h fridge failure).
