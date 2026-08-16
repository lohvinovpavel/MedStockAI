# MedStock AI — Backend Features

What has to exist server-side for the 19 flows in [userflows.md](userflows.md)
to be real rather than mocked.

Grounded in `shared/medstock_shared/models.py` (12 tables today) and the seven
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
| A4 | Scope enforcement | `require("inventory:read")`-style dependency on every endpoint; `PERMS` in `shared/auth.py` | all seven | applied to every route below | Shared dependency, not a service call | Sets `app.actor_id` / `app.hospital_id` through `session_scope` — the input to both RLS and the audit trigger | all | ⚠️ `PERMS` exists, RLS policies not applied |
| **B. Facility & stock** |||||||||
| B1 | Facility registry | The 4 operated + 2 partner sites behind flow 3; type, parent hospital, geo | `warehouse` | `GET /api/warehouse/facilities` · `GET /api/warehouse/facilities/{id}` · `GET /api/warehouse/locations` | `inventory`, `prediction`, `analogue` and the order pipeline all key on `facility_id` | New `facility` (id, hospital_id, name, type, lat/lon, operated); `stock_snapshot.location_id` becomes a FK to it | 3, 7, 14, 16, 17 | ❌ `location_id` is a bare `Text` today |
| B2 | Facility-scoped stock read | Resolve RxCUI → NDCs → on-hand rows for one facility | `inventory` | `GET /api/inventory/stock?rxcui=&facility_id=` · `GET /api/inventory/items` | Calls the shared `rxnorm.py` client | R `stock_snapshot` (uq `hospital_id, ndc, location_id`), left join `formulary_item` | 3, 4, 7, 14 | ⚠️ spec'd; service serves only `/healthz` |
| B3 | Exposure query | `formulary × stock × shortage` — the "what are we exposed to" join driving the KPI tiles | `inventory` | `GET /api/inventory/exposure` | Reads `ingest`-owned `shortage_event`; feeds `prediction` at-risk | R `formulary_item` ⋈ `stock_snapshot` ⋈ `shortage_event` on `ndc`; no writes | 4, 16 | ❌ |
| B4 | Batch / lot receiving | Flow 5 — lot number, expiry, quantity in; FEFO ordering out | `inventory` | `POST /api/inventory/batches` · `GET /api/inventory/batches?ndc=` | Stock delta notifies `warehouse` location and invalidates `prediction` | New `stock_batch` (hospital_id, ndc, location_id, lot, expiry_date, qty); `stock_snapshot.quantity` becomes a rollup of it | 5, 15 | ❌ — **`stock_snapshot` has no expiry column at all**, so the expiry-waste pitch has no schema |
| B5 | Par level / reorder point | Threshold per facility + NDC that makes "critical" objective instead of a mock tone | `inventory` | `GET/PUT /api/inventory/par-levels` | Input to `prediction` at-risk and to the restock recommendation | New `par_level` (hospital_id, facility_id, ndc, reorder_point, target_qty) | 4, 12 | ❌ (`docs/specs/UX-21`) |
| B6 | Formulary import | What this tenant is allowed to stock | `inventory` | `POST /api/inventory/formulary/import` (CSV) | `analogue` reads `rxcui` to boost search ranking | W `formulary_item`, upsert on `(hospital_id, rxcui)` | 4 | ❌ |
| **C. Drug identity & analogues** |||||||||
| C1 | Drug search (UC-1) | Typed name → RxCUI candidates, `in_formulary` first; explicit selection, never auto-pick | `analogue` | `GET /api/analogue/drugs/search?q=` | Live RxNorm from the service; left-joins `inventory`'s table directly (one shared DB) | R `formulary_item`; live NLM call, not cached into `drug` | 4, 19 | ✅ |
| C2 | Package lookup | RxCUI → NDC list (clinical id → shelf id) | `analogue` | `GET /api/analogue/drugs/{rxcui}/packages` | Feeds B2 and C4 | R `rxnorm_edge`, live RxNorm | 7 | ✅ |
| C3 | Analogue candidate graph | Walk RxNorm relations, filter by form/dose, price via NADAC | `analogue` | `GET /api/analogue/analogues/{rxcui}` | `ingest-rxnorm` (weekly) and `ingest-pricing` (daily) populate its inputs | R `rxnorm_edge` (uq `from,to,relationship`), `drug_price` (uq `ndc,effective_date`) | 7 | ✅ |
| C4 | AI analogue ranking | `ask_ai("analogue")` as a **closed-world filter** — keeps ~5, drops any rxcui not already a candidate, strips non-verbatim citations | `analogue` | same endpoint as C3 · `GET /api/analogue/analogues/ai-status` | Only `analogue` and `prediction` may call `ask_ai()` (§3) | R/W `ai_cache`, upsert on `(type, dedupe_key)`; global, no `hospital_id` — deliberate cross-tenant sharing | 7, 19 | ✅ the one registered task |
| C5 | Local availability overlay | Flow 7's "N units here / Not stocked here" plus the nearest facility holding it | `analogue` | `GET /api/analogue/analogues/{rxcui}?facility_id=` | Joins `inventory`'s `stock_snapshot` and B1's `facility` | R `stock_snapshot` per candidate NDC, grouped by `location_id` | 7 | ❌ — ranking exists, availability does not |
| C6 | Substitution safety check | Deterministic weighted ruleset, no model; publishes the ruleset it scored with | `patient-profiling` | `POST /api/patients/assess` · `GET /api/patients/ruleset` · `POST /api/patients/demand` | Called by web before a switch is confirmed; gates flow 7 | No PHI stored (`OPEN` in §3) — request-scoped assessment only | 7 | ✅ |
| **D. Compliance** |||||||||
| D1 | Certificate status | Derived green/yellow/red per NDC, stamped with `ruleset_version` and `provenance` | `compliance` | `GET /api/compliance/status?ndc=` · `GET /api/compliance/certificates/{ndc}` · `GET /api/compliance/ruleset` | `ingest-certification` writes; web flow 6 reads | R `drug_certification` (unique `ndc`), `certification_finding` (uq `ndc,code,source_ref`) — the colour is re-derivable from findings, not re-fetched | 6 | ✅ |
| D2 | On-demand exploration | Evaluate an NDC that was never polled; TTL'd row | `compliance` | `POST /api/compliance/explore` | Calls openFDA live | W `drug_certification` with `provenance='on_demand'` and `expires_at` set | 6 | ✅ |
| D3 | Compliance export | Flow 18's Export button → a CSV evidence pack | `compliance` | `GET /api/compliance/export/compliance.csv` | Read-only over the audit log it never writes | R `audit_log_entry` ⋈ `certification_finding` | 18 | ❌ spec'd only |
| **E. Forecasting** |||||||||
| E1 | Demand forecast | Per facility + NDC burn rate, horizon, and **quantile** bands — classical time series, not an LLM | `prediction` | `GET /api/prediction/forecast/{rxcui}?facility_id=` | Consumes B2/B4 history; output feeds F1 and flow 9 | New `forecast_point` (facility_id, ndc, run_id, target_date, p10/p50/p90); written by a CronJob, served by one indexed read | 9, 12 | ❌ — **service is `healthz`/`readyz`/`version` only**, while the UI claims Prophet/XGBoost and 94.2% confidence |
| E2 | Days-of-supply / at-risk | The product's core metric; depletion date per SKU | `prediction` | `GET /api/prediction/at-risk?facility_id=` | Joins stock (`inventory`) against shortages (`ingest`) — the join that justified one shared database | R `stock_snapshot` ⋈ `forecast_point` ⋈ `shortage_event` ⋈ `par_level` | 4, 9, 16 | ❌ |
| E3 | Surge scenario | Flow 10's 100→300% multiplier applied server-side so the number is reproducible and auditable | `prediction` | `GET /api/prediction/forecast/{rxcui}?surge_pct=` | Same read path as E2 with a scale factor | No write — a pure function over E1 rows | 10, 11 | ❌ client-side arithmetic today |
| **F. Procurement** |||||||||
| F1 | Restock recommendation | Flow 12's suggestion: quantity, supplier, coverage days, and the forecast run it came from | `prediction` proposes, `inventory` owns the record | `GET /api/prediction/recommendations` · `POST /api/inventory/recommendations/{id}/approve` · `POST /api/inventory/recommendations/{id}/reject` | Reads E1 + B5 + F2 pricing | New `review_decision` — already assumed by the audit trigger in §1.3 but **absent from `models.py`**; insert as `pending` | 12, 13 | ❌ |
| F2 | Supplier + catalog | Suppliers, lead time, reliability, per-SKU unit cost — what drives flow 14's live estimate | `warehouse` | `GET /api/warehouse/suppliers` · `GET /api/warehouse/suppliers/{id}/catalog` | Order pricing reads it; `ingest-pricing` can seed it from NADAC | New `supplier`, `supplier_catalog` (supplier_id, ndc, unit_cost, uq pair) | 14 | ❌ |
| F3 | Purchase order lifecycle | `draft → placed → in_transit → delivered / cancelled`, from both entry points | `inventory` | `POST /api/inventory/orders` · `PATCH /api/inventory/orders/{id}/status` · `DELETE /api/inventory/orders/{id}` | A delivery event writes stock through B4; every status change lands in H1 | New `purchase_order` + `purchase_order_line`; `status` a `CHECK` constraint, transitions guarded server-side rather than in React | 12, 13, 14, 15 | ❌ — entirely `OrdersProvider` in browser memory |
| F4 | Order history query | Flow 15's filterable table, scoped to hospital and facility | `inventory` | `GET /api/inventory/orders?status=&facility_id=` | — | R `purchase_order` ⋈ `supplier` ⋈ `facility`; index on `(hospital_id, status, created_at DESC)` | 15 | ❌ |
| **G. Redistribution** |||||||||
| G1 | Shortage matrix | Coverage per facility for one alert, surplus donors ranked by distance | `inventory` | `GET /api/inventory/shortages` · `GET /api/inventory/shortages/{id}/coverage` | Reads `ingest`'s `shortage_event`; B1 supplies distance | R `stock_snapshot` grouped by `location_id` ⋈ `shortage_event` | 16 | ❌ mock `shortageMatrix` |
| G2 | Inter-facility transfer | Flow 17 — the request, the dispatch reference, and the two stock movements it implies | `warehouse` | `POST /api/warehouse/transfers` · `GET /api/warehouse/transfers` · `PATCH /api/warehouse/transfers/{id}/status` | Debits source and credits destination through `inventory` B4; can close an F3 need instead of ordering | New `transfer_request` (from/to facility, ndc, qty, status, ref); the stock move is one transaction, not two writes | 17 | ❌ — a toast that changes nothing |
| **H. Audit** |||||||||
| H1 | Append-only audit log | Flow 18's trail, written by a **trigger** rather than by application code | Postgres itself; read through `compliance` | `GET /api/compliance/audit?entity=&entity_id=` | Every service's writes land here via `write_audit_entry()` reading `app.actor_id` | New `audit_log_entry`; `REVOKE UPDATE, DELETE ON audit_log_entry FROM app_role` — append-only is a grant, not a convention | 8, 18 | ❌ — **§1.3 documents the trigger; `models.py` has neither `audit_log_entry` nor `review_decision`** |
| H2 | AI decision provenance | Make an AI answer replayable: which model, which prompt version, which cache key | `analogue` / `prediction` via `shared/ai.py` | surfaced on C4 and F1 responses | — | Add `model` and `prompt_version` to `ai_cache` and to `AITask`; log `dedupe_key` on the audit row whenever the actor is not human | 7, 12, 18, 19 | ❌ — today a prompt edit silently changes answers under the same `dedupe_key` |
| **I. Copilot** |||||||||
| I1 | Chat with tool calling | Flow 19 — one tool per existing endpoint (`stock`, `analogues`, `certificates`, `forecast`) | new thin gateway, or `analogue` extended | `POST /api/copilot/messages` (SSE) | Forwards the **caller's** JWT on every tool call, so the copilot inherits A4 scopes per tool — far easier to defend than a service account | R/W `ai_cache`; no new tables needed for the cards, which map onto existing responses | 19 | ❌ canned replies (`docs/specs/UX-09`) |
| I2 | Conversation persistence | Survive a reload, and give the audit log something to point at | copilot gateway | `GET /api/copilot/conversations/{id}` | H1 | New `copilot_message` (hospital_id, actor_id, role, text, card JSONB) | 19 | ❌ |

---

## Three structural gaps

1. **`audit_log_entry` and `review_decision` do not exist.** `docs/services.md` §1.3 builds
   the entire compliance story on a trigger writing to tables that were never added to
   `shared/medstock_shared/models.py`. Every "AI suggested / pharmacist approved" claim in
   the UI depends on H1 existing first.
2. **`stock_snapshot` has no batch, lot, or expiry.** B4 is not a feature layered on top of
   the schema — it *is* a schema change that the expiry-waste and FEFO pitch already assumes.
3. **`prediction` is empty.** E1–E3 and F1 all hang off it. Build it as a CronJob writing
   `forecast_point` rows and one indexed read serving them. Do **not** fill the
   `# prediction — Mykhailo` slot in `shared/medstock_shared/ai_tasks.py` with a Gemini
   prompt: forecasting is a time-series problem, and routing it through an LLM gives up the
   reproducibility that E3 and H2 exist to provide.

## One schema fix to do first

`hospital_id` is `Text` on the tenant tables but `UUID` on `hospital` — flagged in
`models.py` as parallel-authoring drift between owners. Resolve it in one migration before
adding the ten tables above and propagating the wrong type into all of them.
