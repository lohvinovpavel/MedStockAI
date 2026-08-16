# RBAC Matrix — MedStock AI

Roles map onto the existing JWT `role` claim (`shared/medstock_shared/auth.py`, `PERMS`):

| Product role | `role` claim | Notes |
|---|---|---|
| Chief Pharmacist | `pharmacist` | clinical owner of the formulary, HITL approver |
| Procurement Officer | `admin` | buys stock, owns suppliers and POs |
| Clinical Director | `director` | oversight, cost & compliance, no operational writes |
| Doctor | `physician` | point-of-order consumer only |

Access legend: **F** = full (read + write/act) · **R** = read-only · **—** = no access.

LLM-tool legend:
- **Yes** — safe to expose as a Copilot tool; read-only or reversible.
- **Draft** — Copilot may *prepare* the action, a human clicks the commit button. Tool returns a proposal, never a side effect.
- **No** — never a tool: irreversible, regulated, or auth-critical.

## Matrix

| # | Feature | Where | Chief Pharmacist | Procurement Officer | Clinical Director | Doctor | LLM tool? |
|---|---|---|---|---|---|---|---|
| 1 | Stock levels & batch list (per facility) | `/inventory` | F | R | R | R | Yes — `get_stock(sku, facility)` |
| 2 | Search / filter by SKU, INN, ATC, expiry | `/inventory` | F | F | F | F | Yes — `search_drugs(query)` |
| 3 | Receive batch (goods-in) | `/inventory` | F | F | — | — | No — physical custody event |
| 4 | Expiry & near-expiry tracking | `/inventory` | F | R | R | R | Yes — `list_expiring(days)` |
| 5 | Certificate / registration check | `/inventory`, Copilot | F | R | R | R | Yes — `check_certificate(ndc)` |
| 6 | Consumption history & burn rate | `/forecasts` | R | R | R | — | Yes — `get_usage(sku, window)` |
| 7 | 30-day depletion forecast | `/forecasts` | R | R | R | R | Yes — `forecast_depletion(sku)` |
| 8 | Surge / what-if simulation | `/forecasts` | F | F | F | — | Yes — `simulate_surge(sku, pct)` |
| 9 | Emergency supply plan (air freight, cost premium) | `/forecasts` → Copilot | F | F | R | — | Draft |
| 10 | Restock suggestion: accept / edit qty / decline | `/forecasts` | F | F | — | — | Draft |
| 11 | Create purchase order | `/orders` | F | F | — | — | Draft — `draft_po(sku, qty)` |
| 12 | Draft PO review queue | `/orders` | R | F | R | — | Yes (read) / Draft (edit) |
| 13 | **Place order** (commit to supplier) | `/orders` | — | F | — | — | No — spends money |
| 14 | Discard draft | `/orders` | F | F | — | — | Draft |
| 15 | Order history, supplier & cost detail | `/orders` | R | F | R | — | Yes — `list_orders(filters)` |
| 16 | Supplier catalogue, pricing, lead times | `/orders` | R | F | R | — | Yes — `get_supplier_offers(sku)` |
| 17 | Shortage alerts feed | `/shortages` | F | F | R | R | Yes — `list_shortages()` |
| 18 | Regional / facility coverage matrix | `/shortages` | F | F | R | — | Yes — `facility_coverage(sku)` |
| 19 | Inter-facility transfer request | `/shortages` | F | F | — | — | Draft |
| 20 | Bio-equivalent / analogue search + ranking | Copilot, `/inventory` | F | R | R | R | Yes — `find_analogues(sku)` |
| 21 | **Approve / reject substitution** (HITL) | Copilot, queue | F | — | — | — | No — licensed act, `recommendation:approve` |
| 22 | Point-of-order substitution prompt | Doctor view | R | — | — | F | Yes — `substitution_advice(sku)` |
| 23 | Formulary write (add/retire SKU) | admin | — | F | — | — | No |
| 24 | Audit log (who decided what, when, why) | `/audit` | R | R | F | — | Yes — `query_audit(filters)` |
| 25 | Export audit trail to compliance archive | `/audit` | — | — | F | — | No — regulated export |
| 26 | Savings / cost-avoidance dashboard | `/audit`, director view | R | R | F | — | Yes — `get_savings(period)` |
| 27 | Facility switcher | side nav | F (own sites) | F | F (all) | — | Yes — `list_facilities()` |
| 28 | AI Copilot chat | drawer | F | F | F | F | n/a — the surface itself |
| 29 | Profile & settings | user menu | F | F | F | F | No — auth surface |
| 30 | User / role management | admin | — | — | — | — | No — `admin` only, out of app scope |

## Tool-exposure rule

Every **Draft** tool returns a proposal object rendered as a Copilot card with an explicit
human commit button (`Place order`, `Accept`, `Request transfer`). The model never calls the
committing endpoint — matching the existing HITL rule that the LLM never prescribes on its own.

Copilot tool calls inherit the caller's JWT, so the same `PERMS` check gates the tool as gates
the UI. No separate tool ACL.

## Gap vs. current code

`PERMS` today has no procurement permissions. To back this matrix, add:

```python
"pharmacist": {..., "order:draft", "transfer:request", "forecast:read"},
"admin":      {..., "order:draft", "order:place", "supplier:read", "transfer:request"},
"director":   {..., "audit:export", "savings:read", "forecast:read"},
"physician":  {..., "substitution:read"},
```
