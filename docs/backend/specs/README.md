# Backend feature specs

One file per feature from [../backend-features.md](../backend-features.md). Each spec is written
to be implementable without reading the conversation that produced it: goal, endpoints with
request/response bodies, DDL, business rules, failure modes, acceptance criteria, and an explicit
out-of-scope list.

Read first: [../userflows.md](../userflows.md) (what the UI does) →
[../backend-features.md](../backend-features.md) (what must exist) →
[../db-schema.md](../db-schema.md) (the tables and migration order) → the spec you are building.

**Specs for ✅ features are short on purpose.** They record why the existing design is correct
and what not to change, rather than restating shipped code.

## Index

| Spec | Feature | Service | Status |
|---|---|---|---|
| [A1](A1-login-token-issue.md) | Login and token issue | `auth` | ✅ |
| [A2](A2-mfa-otp.md) | MFA / OTP step | `auth` | ❌ |
| [A3](A3-session-identity.md) | Session identity | `auth` | ✅ |
| [A4](A4-scope-enforcement-rls.md) | Scope enforcement and RLS | all seven | ✅ |
| [B1](B1-facility-registry.md) | Facility registry | `warehouse` | ✅ |
| [B2](B2-facility-scoped-stock.md) | Facility-scoped stock read | `inventory` | ✅ |
| [B3](B3-exposure-query.md) | Exposure query | `inventory` | ✅ |
| [B4](B4-batch-lot-receiving.md) | Batch / lot receiving and FEFO | `inventory` | ✅ |
| [B5](B5-par-levels.md) | Par level / reorder point | `inventory` | ✅ |
| [B6](B6-formulary-import.md) | Formulary import | `inventory` | ✅ |
| [C1](C1-drug-search.md) | Drug search (UC-1) | `analogue` | ✅ |
| [C2](C2-package-lookup.md) | Package lookup | `analogue` | ✅ |
| [C3](C3-analogue-candidate-graph.md) | Analogue candidate graph | `analogue` | ✅ |
| [C4](C4-ai-analogue-ranking.md) | AI analogue ranking | `analogue` | ✅ |
| [C5](C5-local-availability-overlay.md) | Local availability overlay | `analogue` | ✅ |
| [C6](C6-substitution-safety.md) | Substitution safety check | `patient-profiling` | ✅ |
| [D1](D1-certificate-status.md) | Certificate status | `compliance` | ✅ |
| [D2](D2-on-demand-exploration.md) | On-demand exploration | `compliance` | ✅ |
| [D3](D3-compliance-export.md) | Compliance export | `compliance` | ❌ |
| [E1](E1-demand-forecast.md) | Demand forecast | `prediction` | ✅ |
| [E2](E2-days-of-supply-at-risk.md) | Days of supply / at-risk | `prediction` | ✅ |
| [E3](E3-surge-scenario.md) | Surge scenario | `prediction` | ✅ |
| [F1](F1-restock-recommendation.md) | Restock recommendation | `prediction` + `inventory` | ❌ |
| [F2](F2-supplier-catalog.md) | Supplier and catalog | `warehouse` | ✅ |
| [F3](F3-purchase-order-lifecycle.md) | Purchase order lifecycle | `inventory` | ❌ |
| [F4](F4-order-history.md) | Order history query | `inventory` | ❌ |
| [G1](G1-shortage-matrix.md) | Shortage matrix | `inventory` | ✅ |
| [G2](G2-inter-facility-transfer.md) | Inter-facility transfer | `warehouse` | ❌ |
| [H1](H1-append-only-audit-log.md) | Append-only audit log | Postgres trigger | ✅ |
| [H2](H2-ai-decision-provenance.md) | AI decision provenance | `shared/ai.py` | ❌ |
| [I1](I1-copilot-tool-calling.md) | Copilot chat with tool calling | copilot gateway | ❌ |
| [I2](I2-copilot-persistence.md) | Copilot conversation persistence | copilot gateway | ❌ |

## Build order

Dependencies, not priorities. Each wave is buildable in parallel by different owners once the
previous wave lands.

| Wave | Specs | Why here |
|---|---|---|
| 0 | `hospital_id` uuid migration (A4) | ✅ landed (`20260818_hospital_uuid`) |
| 1 | B1 UI cutover, **H1** | ✅ landed. Sidebar reads `GET /warehouse/facilities`; audit trigger on `review_decision` |
| 2 | A4 policies, **B2** `/items`, **B4**, **B5** | ✅ landed (`20260818_wave2_stock`). Inventory table is live |
| 3 | B3, B6, C5 | ✅ landed (`20260818_wave3`). Formulary CSV, exposure KPIs, analogue overlay on live `stock_snapshot`. Remaining mock after wave 4 is orders/copilot |
| 4 | **F2**, G1 | ✅ landed (`20260818_wave4`). Pricing catalog + shortage matrix live. Remaining mock is orders/copilot. E2/E3 already live on `/forecasts`. Wave 4 as asked is G1+F2 only; F1/F3/F4/G2 stay wave 5. |
| 5 | **F1**, **F3**, F4, G2 | The order pipeline, end to end |
| 6 | D3, H2, I1, I2 | Export, provenance, copilot |

Bold entries are on the critical path — every wave after them is blocked until they land.

## Conventions used in every spec

- Endpoints show the ingress path from `docs/services.md` §3 and the scope required (A4).
- DDL is the target shape, not a migration script. Alembic autogenerate reads `Base.metadata`,
  so every table must first be imported into `shared/medstock_shared/models.py`.
- Tenant tables never carry an application `WHERE hospital_id` — RLS plus `session_scope` are
  the filter. A spec that seems to need one has a missing policy.
- Degradation is specified, not assumed: an upstream failure returns partial data with a flag,
  never a 500 (the `AIError` contract in `shared/medstock_shared/ai.py` generalised).
- Every spec ends with an out-of-scope list. It is there to stop scope creep during
  implementation, and it is as binding as the rest of the file.
