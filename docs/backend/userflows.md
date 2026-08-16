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
| 1 | Sign in | Any | `/` landing CTA | Landing → `/login` → email + password → OTP step → submit code | Toast "Signed in" → `/inventory` |
| 2 | Demo role sign-in | Pharmacist / Procurement / Clinical Director | `/login` | Click "Demo Login as {role}" | Toast with role → `/inventory` (role is cosmetic — no gating) |
| 3 | Switch facility | Any | Sidebar bottom dropdown | Open dropdown → pick one of 4 `operated` facilities | `FacilityProvider` updates → inventory list, KPIs, analogue availability, shortage "this facility", audit SKU picker all re-derive |
| 4 | Triage inventory | Pharmacist | `/inventory` | Search by SKU/INN/ATC → filter status → filter expiry date range → click row | Row selected, Copilot focus set to that SKU |
| 5 | Receive a batch | Pharmacist | `/inventory` → **Receive Batch** | Dialog → drug, batch no., qty → Save | Toast "Batch received" (mock — list not mutated) |
| 6 | Verify certificate | Compliance | Row `⋯` → **View certificate** | Dialog → authority, number, status, expiry | Dialog closed; no state change |
| 7 | Find analogues | Pharmacist | Row `⋯` → **Find analogues** | Dialog → list sorted `matchScore` desc → per row: equivalence + source badge, "N units here" vs "Not stocked here" → nearest facility that stocks it | Substitution decision made; feeds flow 17 if only elsewhere has it |
| 8 | Read SKU history | Compliance | Row `⋯` → **Audit Log** | `router.push('/audit?sku=<id>')` → trail preselected to that SKU | Lands in flow 18 |
| 9 | Review a forecast | Pharmacist | `/forecasts` | Select SKU → 60d actuals vs 30d forecast, confidence band, depletion `ReferenceLine` | Depletion horizon known |
| 10 | Scenario simulation | Clinical Director | `/forecasts` slider | Drag Standard 100% → Epidemic Surge 300% | Burn-rate curve recalculates live, depletion shifts 14d → 3d |
| 11 | Emergency supply plan | Clinical Director | `/forecasts` → **Generate emergency supply plan** | Button carries `{drugName, surgePct, depletionDays}` → Copilot drawer opens | Copilot renders `emergency` card: air-freight days + cost premium |
| 12 | AI restock suggestion | Procurement | `/forecasts` PO card | **Decline** → card collapses to "dismissed — Restore" · **Adjust Quantity** → inline edit · **Create Draft Order** | Draft `PO-2026-####` (`source: ai_suggestion`, `status: draft`) + sidebar badge +1 + toast with "Review" → `/orders` |
| 13 | Review AI drafts | Procurement | `/orders` review queue | Draft card → **Place** or **Discard** | Place → `status: placed`, drops into history, badge −1 · Discard → removed, toast |
| 14 | Manual purchase order | Procurement | `/orders` new-order form | Facility (defaults to active) → supplier → SKU (from that facility's stock) → qty → live estimate (unit×qty + shipping, lead time, ETA) → **Place Order** | `status: placed`, `source: manual`, appears in history immediately |
| 15 | Track order history | Procurement | `/orders` history table | Filter by status → read PO ref, date, facility, supplier, qty, total, source badge | Lifecycle visible: `draft → placed → in_transit → delivered` (`cancelled` terminal) |
| 16 | Triage a shortage | Clinical Director | `/shortages` | Pick alert → facility matrix with coverage tone (stockout/critical/normal/surplus) → filter by name | Surplus donors identified, distances relative to active facility |
| 17 | Inter-facility transfer | Clinical Director | `/shortages` transfer card | Select source facility (surplus only) → qty → **Request transfer** | Dispatch reference + timestamp rendered |
| 18 | Audit & compliance trail | Compliance | `/audit` or deep link from flow 8 | Pick SKU → chronological entries (human confirmations, ML pipeline runs, OCR cert verification) → **Export** | Toast "exported to compliance archive" |
| 19 | Ask the Copilot | Any | Right drawer, any page | Free text, or quick action **Generate PO** / **Find Bio-Equivalent** / **Check Certificate** — scoped to current focus if a row is selected | Structured card: `po` \| `analogues` \| `certificate` \| `emergency` |

---

## Composite journeys (where flows chain)

| Journey | Chain | Why it matters |
|---|---|---|
| Forecast → procurement | 3 → 9 → 10 → 12 → 13 → 15 | The one true pipeline: two entry points (AI draft, manual form) write to a single `OrdersProvider`; `/orders` is the only reader |
| Stockout → substitution | 4 → 7 → 16 → 17 | Analogue not stocked locally → shortage matrix finds the donor → transfer dispatched, no PO needed |
| Incident → evidence | 4 → 6 → 8 → 18 | Every substitution/cert decision terminates in an exportable trail — the ISO-13485 story |

---

## Known gaps in the current mock

- Role selected in flow 2 gates nothing — `PERMS` exists in `shared/medstock_shared/auth.py` but the UI never reads it.
- Flow 5 does not mutate inventory; the dialog input is discarded (`docs/specs/UX-08`).
- Flows 13/15 have no path to `in_transit` / `delivered` — those statuses appear only in seeded rows.
- Flow 17's transfer never becomes an order and never moves stock.
- Flow 10's surge multiplier is client-side arithmetic and does not drive flow 12's suggested quantity (`docs/specs/UX-04`).
