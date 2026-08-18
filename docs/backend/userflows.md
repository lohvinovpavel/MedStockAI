# MedStock AI — User Flows

Derived from the shipped mock dashboard in `web/` (pages, dialogs, and context
providers as wired today), not from marketing copy. Each row is one flow from
its entry point to its terminal state.

Related: [backend-features.md](backend-features.md) maps these flows onto the
services and tables that have to exist behind them.

---

## User flows

| # | Flow | Actor | Entry point | Steps (start → end) | End state |
|---|---|---|---|---|---|
| 1 | Sign in | Any | `/` landing CTA | Landing → `/login` → email + password → OTP step (UI-only, A2 unused) → submit | Toast "Signed in" → that role's `HOME_ROUTE` |
| 2 | Demo role sign-in | Pharmacist / Procurement / Clinical Director | `/login` | Click "Demo Login as {role}" | Toast with role → that role's `HOME_ROUTE` (`/inventory`, `/orders`, `/audit`). Nav and pages are gated by `PAGE_ROLES`; endpoints still 403 on their own `PERMS` |
| 3 | Switch facility | Any | Sidebar bottom dropdown | Open dropdown → pick one of 4 `operated` facilities from `GET /warehouse/facilities?operated=true` | `FacilityProvider` stores B1 `code`; inventory reloads `GET /items?facility_id=`; shortage matrix reloads `GET /shortages` + coverage; orders reload `GET /orders`; sidebar distances are haversine from the **selected** site |
| 4 | Triage inventory | Pharmacist | `/inventory` | Search by SKU/INN/ATC → filter status → filter expiry date range → click row | Row selected, Copilot focus set to that SKU |
| 5 | Receive a batch | Pharmacist | `/inventory` → **Receive Batch** | Dialog → NDC, lot, qty, expiry → Save | Toast "Batch received"; `POST /batches` writes Postgres and the table reloads from `GET /items` |
| 6 | Verify certificate | Compliance | Row `⋯` → **View certificate** | Dialog → authority, number, status, expiry | Dialog closed; no state change |
| 7 | Find analogues | Pharmacist | Row `⋯` → **Find analogues** | Dialog → list sorted `matchScore` desc → per row: equivalence + source badge, "N units here" vs "Not stocked here" → nearest facility that stocks it | Substitution decision made; feeds flow 17 if only elsewhere has it |
| 8 | Read SKU history | Compliance | Row `⋯` → **Audit Log** | `router.push('/audit?sku=<id>')` → trail preselected to that SKU | Lands in flow 18 |
| 9 | Review a forecast | Pharmacist | `/forecasts` | Select SKU → 60d actuals vs 30d forecast, confidence band, depletion `ReferenceLine` | Depletion horizon known |
| 10 | Scenario simulation | Clinical Director | `/forecasts` slider | Drag Standard 100% → Epidemic Surge 300% | Burn-rate curve recalculates live, depletion shifts 14d → 3d |
| 11 | Emergency supply plan | Clinical Director | `/forecasts` → **Generate emergency supply plan** | Button carries `{drugName, surgePct, depletionDays}` → Copilot drawer opens | Copilot renders `emergency` card: air-freight days + cost premium |
| 12 | AI restock suggestion | Procurement | Copilot **Generate PO** or `GET /prediction/recommendations` | Card from live F1 → **Create Draft Order** materialises a `review_decision` and approves it | Draft `PO-2026-####` (`source: ai_suggestion`, `status: draft`) + sidebar badge +1 + toast with "Review" → `/orders` |
| 13 | Review AI drafts | Procurement | `/orders` review queue | Draft card → **Place** (`PATCH …/status`) or **Discard** (`DELETE`, draft only) | Place → `status: placed`, drops into history, badge −1 · Discard → removed, toast |
| 14 | Manual purchase order | Procurement | `/orders` new-order form | Facility (defaults to active) → supplier (`GET /warehouse/suppliers`) → SKU (`GET /items`) → qty → live `POST /quote` → **Place Order** (`POST /orders`) | `status: placed`, `source: manual`, appears in history immediately |
| 15 | Track order history | Procurement | `/orders` history table | Filter by status → read PO ref, date, facility, supplier, qty, total, source badge from `GET /orders` | Lifecycle visible: `draft → placed → in_transit → delivered` (`cancelled` terminal) |
| 16 | Triage a shortage | Clinical Director | `/shortages` | Pick alert → facility matrix with coverage tone (stockout/critical/normal/surplus) → filter by name | Surplus donors identified, distances relative to active facility |
| 17 | Inter-facility transfer | Clinical Director | `/shortages` transfer card | Select source facility (surplus only) → qty → **Request transfer** (`POST /warehouse/transfers` then `PATCH …/status` dispatched) | Dispatch reference + timestamp rendered; source stock debited |
| 18 | Audit & compliance trail | Compliance | `/audit` or deep link from flow 8 | Timeline from `GET /audit`. SKU picker is `GET /items`. Certificate badge and DecisionTrail are live. **Export** downloads `GET /export/compliance.csv` | CSV evidence pack |
| 19 | Ask the Copilot | Any | Right drawer, any page | Free text, or quick action **Generate PO** / **Find Bio-Equivalent** / **Check Certificate** — scoped to current focus if a row is selected | Structured card: `po` \| `analogues` \| `certificate` \| `emergency` |

---

## Composite journeys (where flows chain)

| Journey | Chain | Why it matters |
|---|---|---|
| Forecast → procurement | 3 → 9 → 10 → 12 → 13 → 15 | The one true pipeline: two entry points (AI draft, manual form) write `purchase_order`; `/orders` reads `GET /orders` |
| Stockout → substitution | 4 → 7 → 16 → 17 | Analogue not stocked locally → shortage matrix finds the donor → transfer dispatched, no PO needed |
| Incident → evidence | 4 → 6 → 8 → 18 | Every substitution/cert decision terminates in an exportable trail — the ISO-13485 story |

---

## Data source per screen

User-facing screens must not mix a mock catalog with a live API on the same table.

| Screen | Source of truth |
|---|---|
| Login, session, nav gating | Postgres via `auth` |
| Analogues, Prescribe | Postgres + RxNorm (+ Gemini on Full) via `analogue` / `patients`. Analogue list overlay is live `stock_snapshot` when `facility_id` is sent |
| Warehouse | Postgres via `warehouse` |
| Restock & Forecasts | Postgres via `prediction` |
| Prognosis Review | Postgres via `patients` |
| Certificate badges (any page that has an NDC) | Postgres via `compliance` |
| Sidebar facility switcher | Postgres via `warehouse` (`GET /facilities?operated=true`; `code` is the client key) |
| Inventory & Batches | Postgres via `inventory` (`GET /items`, `POST /batches`, `GET /exposure`). Status is B5-derived; lot/expiry from B4; `in_formulary` from B6 |
| Purchase & Orders | Postgres via `inventory` (`GET /orders`, `POST /orders`) and `warehouse` (`GET /suppliers`, `POST /quote`) |
| Shortage Matrix | Postgres via `inventory` (`GET /shortages`, `GET /shortages/{id}/coverage`) plus `POST /warehouse/transfers` for G2 |
| Audit Log | Postgres via `compliance` `GET /audit` and `GET /export/compliance.csv`. SKU picker is `GET /items` |
| Copilot cards | Live F1 / analogue / compliance / forecast; free text via `POST /api/copilot/messages`. Footer telemetry is `web/lib/system-status.ts` |

Waves 5–6 cut over orders, transfers, export, and copilot. There is no `mock-data.ts`.
Wave 3 cut over formulary, exposure, and the analogue availability overlay.
Wave 4 cut over the shortage matrix (G1) and the supplier/price catalog (F2).

## Known gaps

- Flow 2 demo login is real auth (`ann@stmarys.org` etc.); OTP (A2) is still UI-only and unused.
- Flow 12's restock card on `/forecasts` is not yet a dedicated F1 widget — copilot Generate PO and `GET /prediction/recommendations` are the live path.
- Flow 15's in_transit / delivered statuses appear from seed and from `PATCH /orders/{id}/status`; the orders page itself does not ship/receive.
