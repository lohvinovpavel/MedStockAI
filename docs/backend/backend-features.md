# MedStock AI — Backend Features

What has to exist server-side for the 19 flows in [userflows.md](userflows.md)
to be real rather than mocked.

Grounded in `shared/medstock_shared/models.py` and the seven
services described in [../services.md](../services.md) §3. Ingress paths follow
that spec: `/api/auth`, `/api/inventory`, `/api/analogue`, `/api/compliance`,
`/api/patients`, `/api/prediction`, `/api/warehouse`.

Each feature id links to a full implementation spec in [specs/](specs/README.md) — endpoints,
DDL, business rules, failure modes, acceptance criteria, and out-of-scope boundaries.

**Status legend** — ✅ implemented · ⚠️ partial or spec'd only · ❌ missing.

---

## Feature inventory

| # | Feature | Description | Service | API endpoints | Relations | DB interaction | Flows | Status |
|---|---|---|---|---|---|---|---|---|
| **A. Identity & access** |||||||||
| A1 | Login + token issue | Verify credentials, lock after N failures, issue signed JWT carrying `hospital_id` + `role` | `auth` | `POST /api/auth/login` · `POST /api/auth/logout` | Holds the private signing key; all six others verify locally (§1.4) — no callback | R `app_user`, `membership`, `hospital` (direct `SessionLocal`, pre-tenant, no RLS); W `app_user.failed_attempts`, `locked_until` | 1, 2 | ✅ |
| A2 | MFA / OTP step | Flow 1's second screen — issue and verify a one-time code | `auth` | `POST /api/auth/login/otp` · `POST /api/auth/login/otp/resend` | — | New `otp_challenge` (user_id, code_hash, expires_at, attempts); insert, then delete on success | 1 | ❌ UI-only |
| A3 | Session identity | Role, hospital, and granted scopes so the UI can gate nav instead of decorating it | `auth` | `GET /api/auth/me` | Web reads it to hide/show nav items and row actions | R `membership`, `app_user` | 2, 3 | ✅ |
| A4 | Scope enforcement | `require("inventory:read")`-style dependency on every endpoint; `PERMS` in `shared/auth.py` | all seven | applied to every route below | Shared dependency, not a service call | Sets `app.actor_id` / `app.hospital_id` through `session_scope` — the input to both RLS and the audit trigger | all | ✅ FORCE RLS on tenant tables (wave 2); identity/reference tables exempt |
| **B. Facility & stock** |||||||||
| B1 | Facility registry | The 4 operated + 2 partner sites behind flow 3; type, parent hospital, geo | `warehouse` | `GET /api/warehouse/facilities` · `GET /api/warehouse/facilities/{id}` · `GET /api/warehouse/locations` | `inventory`, `prediction`, `analogue` and the order pipeline all key on `facility_id` | `facility` + `storage_location`; `stock_snapshot` gained a `facility_id` FK (`location_id` survives as the intra-facility shelf code = `storage_location.code`) | 3, 7, 14, 16, 17 | ✅ API + sidebar cutover (`code` is the client key) |
| B7 | Storage conditions & excursions | Hourly temp/humidity per storage location, checked against class-level requirements on `drug` (refrigerated 2–8 °C, CRT 15–25 °C, freezer); violations computed on read incl. misplaced cold-chain stock | `warehouse` | `GET /api/warehouse/stock` · `GET /api/warehouse/consumption` · `GET /api/warehouse/locations/{id}/conditions` · `GET /api/warehouse/excursions` | Consumption history (`consumption_daily`, 3y daily, stockout-censored) is what E1 forecasts from — see `docs/demo-data.md` §6a for the planted-signal contract | R `location_condition` ⋈ `storage_location` ⋈ `stock_snapshot` ⋈ `drug`; W by `seed_demo` only until real sensors/B4 exist | 3 | ✅ issue #8 |
| B2 | Facility-scoped stock read | Resolve RxCUI → NDCs → on-hand rows for one facility | `inventory` | `GET /api/inventory/stock?rxcui=&facility_id=` · `GET /api/inventory/items` | Calls the shared `rxnorm.py` client | R `stock_snapshot` (uq `hospital_id, ndc, facility_id, location_id` since B1), left join `formulary_item`; status from `par_level` | 3, 4, 7, 14 | ✅ wave 2 — inventory page reads `/items` |
| B3 | Exposure query | `formulary × stock × shortage` — the "what are we exposed to" join driving the KPI tiles | `inventory` | `GET /api/inventory/exposure` | Reads `ingest`-owned `shortage_event`; feeds `prediction` at-risk | R `formulary_item` ⋈ `stock_snapshot` ⋈ `shortage_event` on `ndc`; no writes | 4, 16 | ✅ wave 3 |
| B4 | Batch / lot receiving | Flow 5 — lot number, expiry, quantity in; FEFO ordering out | `inventory` | `POST /api/inventory/batches` · `GET /api/inventory/batches?ndc=` · `POST /api/inventory/batches/{id}/consume` | Stock delta notifies `warehouse` location and invalidates `prediction` | `stock_batch` (hospital_id, facility_id, ndc, lot, expiry_date, qty); `stock_snapshot.quantity` is a rollup trigger | 5, 15 | ✅ wave 2 |
| B5 | Par level / reorder point | Threshold per facility + NDC that makes "critical" objective instead of a mock tone | `inventory` | `GET/PUT /api/inventory/par-levels` | Input to `prediction` at-risk and to the restock recommendation | `par_level` (hospital_id, facility_id, ndc, reorder_point, target_qty) | 4, 12 | ✅ wave 2 |
| B6 | Formulary import | What this tenant is allowed to stock | `inventory` | `POST /api/inventory/formulary/import` (CSV) · `GET/DELETE /api/inventory/formulary` | `analogue` reads `rxcui` to boost search ranking | W `formulary_item`, upsert on `(hospital_id, rxcui)` | 4 | ✅ wave 3 |
| **C. Drug identity & analogues** |||||||||
| C1 | Drug search (UC-1) | Typed name → RxCUI candidates, `in_formulary` first; explicit selection, never auto-pick | `analogue` | `GET /api/analogue/drugs/search?q=` | Live RxNorm from the service; left-joins `inventory`'s table directly (one shared DB) | R `formulary_item`; live NLM call, not cached into `drug` | 4, 19 | ✅ |
| C2 | Package lookup | RxCUI → NDC list (clinical id → shelf id) | `analogue` | `GET /api/analogue/drugs/{rxcui}/packages` | Feeds B2 and C4 | R `rxnorm_edge`, live RxNorm | 7 | ✅ |
| C3 | Analogue candidate graph | Walk RxNorm relations, filter by form/dose, price via NADAC | `analogue` | `GET /api/analogue/analogues/{rxcui}` | `ingest-rxnorm` (weekly) and `ingest-pricing` (daily) populate its inputs | R `rxnorm_edge` (uq `from,to,relationship`), `drug_price` (uq `ndc,effective_date`) | 7 | ✅ |
| C4 | AI analogue ranking | `ask_ai("analogue")` as a **closed-world filter** — keeps ~5, drops any rxcui not already a candidate, strips non-verbatim citations | `analogue` | same endpoint as C3 · `GET /api/analogue/analogues/ai-status` | Only `analogue` and `prediction` may call `ask_ai()` (§3) | R/W `ai_cache`, upsert on `(type, dedupe_key)`; global, no `hospital_id` — deliberate cross-tenant sharing | 7, 19 | ✅ the one registered task |
| C5 | Local availability overlay | Flow 7's "N units here / Not stocked here" plus the nearest facility holding it | `analogue` | `GET /api/analogue/analogues/{rxcui}?facility_id=` | Joins `inventory`'s `stock_snapshot` and B1's `facility` | R `stock_snapshot` per candidate NDC, one grouped query | 7 | ✅ wave 3 — overlay only; ranking unchanged |
| C6 | Substitution safety check | Deterministic weighted ruleset, no model; publishes the ruleset it scored with | `patient-profiling` | `POST /api/patients/assess` · `GET /api/patients/ruleset` · `POST /api/patients/demand` | Called by web before a switch is confirmed; gates flow 7 | No PHI stored (`OPEN` in §3) — request-scoped assessment only | 7 | ✅ |
| **D. Compliance** |||||||||
| D1 | Certificate status | Derived green/yellow/red per NDC, stamped with `ruleset_version` and `provenance` | `compliance` | `GET /api/compliance/status?ndc=` · `GET /api/compliance/certificates/{ndc}` · `GET /api/compliance/ruleset` | `ingest-certification` writes; web flow 6 reads | R `drug_certification` (unique `ndc`), `certification_finding` (uq `ndc,code,source_ref`) — the colour is re-derivable from findings, not re-fetched | 6 | ✅ |
| D2 | On-demand exploration | Evaluate an NDC that was never polled; TTL'd row | `compliance` | `POST /api/compliance/explore` | Calls openFDA live | W `drug_certification` with `provenance='on_demand'` and `expires_at` set | 6 | ✅ |
| D3 | Compliance export | Flow 18's Export button → a CSV evidence pack | `compliance` | `GET /api/compliance/export/compliance.csv` | Read-only over the audit log it never writes | R `audit_log_entry` ⋈ `certification_finding` | 18 | ✅ wave 6 |
| **E. Forecasting** |||||||||
| E1 | Demand forecast | Per facility + NDC burn rate, horizon, and **quantile** bands — classical time series, not an LLM | `prediction` | `GET /api/prediction/forecast/{rxcui}` · `POST /api/prediction/forecast/runs` | Consumes consumption history; output feeds F1 and flow 9 | R/W `forecast_point`; `POST /forecast/runs` writes a run (a CronJob can wrap the same module later) | 9, 12 | ✅ issue #7 — `/forecasts` reads this, not mock-data |
| E2 | Days-of-supply / at-risk | The product's core metric; depletion date per SKU | `prediction` | `GET /api/prediction/at-risk` · `GET /api/prediction/days-of-supply` | Joins stock against shortages — the join that justified one shared database | R `stock_snapshot` ⋈ `forecast_point` ⋈ `shortage_event` | 4, 9, 16 | ✅ on `/forecasts`; B3/G1 attach trailing-mean days |
| E3 | Surge scenario | Flow 10's 100→300% multiplier applied server-side so the number is reproducible and auditable | `prediction` | `GET /api/prediction/forecast/{rxcui}?surge_pct=` | Same read path as E1 with a scale factor | No write — a pure function over E1 rows | 10, 11 | ✅ slider on `/forecasts` sends `surge_pct` |
| **F. Procurement** |||||||||
| F1 | Restock recommendation | Flow 12's suggestion: quantity, supplier, coverage days, and the forecast run it came from | `prediction` proposes, `inventory` owns the record | `GET /api/prediction/recommendations` · `POST /api/inventory/recommendations` · `POST …/approve` · `POST …/reject` | Reads E1 + B5 + F2 pricing | `review_decision` + `purchase_order` | 12, 13 | ✅ wave 5 |
| F2 | Supplier + catalog | Suppliers, lead time, reliability, per-SKU unit cost — what drives flow 14's live estimate | `warehouse` | `GET /api/warehouse/suppliers` · `GET /api/warehouse/suppliers/{id}/catalog` · `POST /api/warehouse/quote` | Order pricing reads it; `ingest-pricing` can seed it from NADAC | `supplier`, `supplier_catalog` | 14 | ✅ wave 4 |
| F3 | Purchase order lifecycle | `draft → placed → in_transit → delivered / cancelled`, from both entry points | `inventory` | `POST /api/inventory/orders` · `PATCH /api/inventory/orders/{id}/status` · `DELETE /api/inventory/orders/{id}` | A delivery event writes stock through B4; every status change lands in H1 | `purchase_order` + `purchase_order_line` | 12, 13, 14, 15 | ✅ wave 5 |
| F4 | Order history query | Flow 15's filterable table, scoped to hospital and facility | `inventory` | `GET /api/inventory/orders?status=&facility_id=` · `GET /orders/summary` | — | R `purchase_order` ⋈ `supplier` ⋈ `facility` | 15 | ✅ wave 5 |
| **G. Redistribution** |||||||||
| G1 | Shortage matrix | Coverage per facility for one alert, surplus donors ranked by distance | `inventory` | `GET /api/inventory/shortages` · `GET /api/inventory/shortages/{id}/coverage` | Reads `ingest`'s `shortage_event`; B1 supplies distance | R `stock_snapshot` grouped by facility ⋈ `shortage_event` | 16 | ✅ wave 4 |
| G2 | Inter-facility transfer | Flow 17 — the request, the dispatch reference, and the two stock movements it implies | `warehouse` | `POST /api/warehouse/transfers` · `GET /api/warehouse/transfers` · `PATCH /api/warehouse/transfers/{id}/status` | Debits source and credits destination through B4 | `transfer_request` | 17 | ✅ wave 5 |
| **H. Audit** |||||||||
| H1 | Append-only audit log | Flow 18's trail, written by a **trigger** rather than by application code | Postgres itself; read through `compliance` | `GET /api/compliance/audit?entity=&entity_id=` | Every service's writes land here via the trigger reading `app.actor_id` | `audit_log_entry`; `REVOKE UPDATE, DELETE` | 8, 18 | ✅ trigger on review_decision, stock_batch, par_level, formulary_item, purchase_order, transfer_request |
| H2 | AI decision provenance | Make an AI answer replayable: which model, which prompt version, which cache key | `analogue` / `prediction` via `shared/ai.py` | surfaced on C4 and F1 responses | — | `model` + `prompt_version` on `ai_cache` and `AITask`; `dedupe_key` includes both; CI fingerprints | 7, 12, 18, 19 | ✅ wave 6 |
| **I. Copilot** |||||||||
| I1 | Chat with tool calling | Flow 19 — one tool per existing endpoint (`stock`, `analogues`, `certificates`, `forecast`, `draft_order`) | analogue (`/api/copilot`) | `POST /api/copilot/messages` (SSE) | Forwards the **caller's** JWT on every tool call | R/W `ai_cache` | 19 | ✅ wave 6 |
| I2 | Conversation persistence | Survive a reload, and give the audit log something to point at | analogue (`/api/copilot`) | `GET/POST/DELETE /api/copilot/conversations` | H1 | `copilot_conversation` + `copilot_message` | 19 | ✅ wave 6 |

---

## Three structural gaps

1. **A2 OTP is still UI-only.** Login skips the second factor.
2. **No scheduled forecast CronJob.** E1–E3 serve `/forecasts` from `forecast_point` via `POST /forecast/runs`. Do **not** fill the `# prediction — Mykhailo` slot in `shared/medstock_shared/ai_tasks.py` with a Gemini prompt: forecasting is a time-series problem.
3. **`/forecasts` does not yet render an F1 restock card.** Copilot Generate PO and `GET /prediction/recommendations` are the live recommendation path.

## Schema already fixed

`hospital_id` is `uuid` on every tenant table (wave 0, `20260818_hospital_uuid`). Do not
reintroduce `Text` hospital ids. Tenant isolation is A4 FORCE RLS (wave 2), not an
application `WHERE hospital_id`.
