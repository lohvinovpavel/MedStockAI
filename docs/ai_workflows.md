# AI Copilot Workflows, by Role

Proposed Gemini-mediated workflows for the four roles this system actually has. Each one takes
something a user does today by visiting three or four screens and turns it into one question or
one button.

**This document is derived from code, not from the other docs in this folder.** Every endpoint,
permission, and table named below was verified against the source as of commit `22d0489`. Where
a workflow needs something that does not exist yet, it says so instead of assuming.

Companion document: [ai_workflow_impl_plan.md](ai_workflow_impl_plan.md) — the engineering plan
for each workflow below.

---

## 1. What the copilot is, in code

- **Surface:** `POST /copilot/chat` (SSE) in [`services/analogue/app/copilot.py`](../services/analogue/app/copilot.py),
  gated on the `copilot:chat` permission. It is the only `async` route in the system.
- **Tools:** a registry in [`shared/medstock_shared/ai/tools/registry.py`](../shared/medstock_shared/ai/tools/registry.py).
  Each tool is a plain sync `fn(args, principal) -> dict` bound to **exactly one** permission,
  declared to Gemini only if the caller's role holds that permission, and re-checked in
  `execute()` before it runs.
- **Tools that exist today** (all in [`tools/pharmacy.py`](../shared/medstock_shared/ai/tools/pharmacy.py)):

  | Tool | Permission | What it reads |
  |---|---|---|
  | `search_analogues_rxnorm` | `drug:search` | RxNorm graph + `stock_snapshot`, ranked by on-hand |
  | `verify_batch_cert` | `certificate:read` | `drug_certification` + `certification_finding` |
  | `check_stock_by_ndc` | `inventory:read` | `stock_snapshot`, grouped by location |

- **Hard rule already enforced in the system prompt:** the model answers only from tool results
  and what the user typed. No tool in the registry writes anything — the copilot may *prepare* an
  action, never commit one.

### 1.1 The permission map, as it really is

Taken from `PERMS` in [`shared/medstock_shared/auth.py`](../shared/medstock_shared/auth.py).
This is what decides which workflows a role can even be offered.

| Permission | pharmacist | physician | director | admin | Endpoints behind it |
|---|:--:|:--:|:--:|:--:|:--:|
| `inventory:read` | ✅ | ✅ | ✅ | ✅ | 5 |
| `drug:search` | ✅ | ✅ | ✅ | ✅ | 4 |
| `facility:read` | ✅ | ✅ | ✅ | ✅ | 5 |
| `certificate:read` | ✅ | ✅ | ✅ | ✅ | 2 |
| `copilot:chat` | ✅ | ✅ | ✅ | ✅ | 1 |
| `forecast:read` | ✅ | — | ✅ | **—** | 4 |
| `forecast:run` | ✅ | — | ✅ | — | 1 |
| `certification:explore` | ✅ | — | **—** | ✅ | 1 |
| `patient:read` / `patient:write` | **—** | ✅ | — | ✅ | 3 / 2 |
| `profile:assess` | ✅ | ✅ | — | — | 3 |
| `profile:explain` | ✅ | ✅ | — | — | 2 |
| `profile:review` | ✅ | — | ✅ | ✅ | 1 |
| `profile:approve` | ✅ | — | — | — | 1 |
| `queue:read` | ✅ | — | — | — | **0** |
| `recommendation:approve` | ✅ | — | — | — | **0** |
| `alert:read` | — | ✅ | — | — | **0** |
| `dashboard:read` | — | — | ✅ | — | **0** |
| `audit:read` | — | — | ✅ | ✅ | **0** |
| `mapping:approve` | — | — | — | ✅ | **0** |
| `formulary:write` | — | — | — | ✅ | **0** |

Three consequences worth naming before designing anything:

1. **Seven permissions have no endpoint behind them.** `queue:read`, `recommendation:approve`,
   `alert:read`, `dashboard:read`, `audit:read`, `mapping:approve`, `formulary:write` are
   declared and granted but nothing calls `require()` on them. Any workflow resting on one of
   these is building the backend, not just the copilot.
2. **The pharmacist cannot read patient rows.** No `patient:read`. Pharmacist-side patient work
   has to go through the `profile:*` permissions, which read assessments, not identities.
3. **Procurement cannot read forecasts.** `admin` has no `forecast:read` — the role that buys
   stock cannot see the depletion curve that justifies buying it. This looks like a genuine gap
   rather than a deliberate restriction, and two workflows below are blocked on it.

### 1.2 Status legend used throughout

| Tier | Meaning |
|---|---|
| **A — ready** | Every input is already in `shared/` or a plain DB query. New tool only. |
| **B — promote** | The logic exists but lives inside a service package the copilot cannot import. Needs lifting into `shared/`. |
| **C — build** | The data or the read does not exist anywhere yet. |

Tier B is the common case and the reason it matters is structural: **no service in this system
calls another over HTTP.** The only `httpx` usage anywhere is to external APIs (openFDA, RxNav).
`analogue` — which hosts the copilot — depends on `medstock-shared` and nothing else, so
anything living in `services/prediction/app/`, `services/compliance/app/explore.py`, or
`services/warehouse/app/main.py` is invisible to a tool.

---

## 2. Chief Pharmacist (`pharmacist`)

The clinical owner of the formulary. Holds the widest permission set of any role, including both
forecast permissions and `certification:explore`.

### PH-1 · Shortage response brief — **Tier A**

> "Propofol is running low — what do I do?"

One question replaces four screens: current stock, how long it lasts, what can substitute for it,
and whether the substitute is compliant.

| | |
|---|---|
| **Joins** | `stock_snapshot` → depletion estimate → RxNorm analogue graph → `drug_certification` |
| **Tools** | `check_stock_by_ndc` + `search_analogues_rxnorm` + `verify_batch_cert` (all three exist) |
| **Replaces** | `/inventory` row → `/forecasts` SKU → analogue dialog → certificate dialog |
| **Output** | On-hand by location, ranked substitutes with their own stock, compliance colour per candidate |
| **Guardrail** | Read-only. Substitution approval stays a human act under `recommendation:approve`. |

The interesting property: this needs **no new tool at all**. Gemini already has all three tools
and can chain them in one turn (`_MAX_TOOL_ROUNDS = 6`). What it needs is a quick-action button
that sends the composed question.

### PH-2 · Formulary-wide certificate sweep — **Tier A**

> "Anything on our shelves gone red?"

| | |
|---|---|
| **Joins** | distinct NDCs in `stock_snapshot` → `drug_certification` + `certification_finding` |
| **Needs** | one new tool, `sweep_shelf_certificates` (`inventory:read`) |
| **Replaces** | scrolling `/inventory` and reading badges row by row |
| **Output** | Only the exceptions — every red/yellow NDC with its finding codes and on-hand quantity |
| **Guardrail** | `unknown` is reported as unknown, never as clean. |

Cheapest real workflow in this document: both halves are already single DB reads and the
`signal()` aggregation is already in `shared/certification.py`.

### PH-3 · Cold-chain excursion digest — **Tier B**

> "Any storage problems this morning?"

| | |
|---|---|
| **Joins** | `location_condition` telemetry × `stock_snapshot` placement × `drug` storage class |
| **Blocked on** | the excursion query lives in `services/warehouse/app/main.py`, not `shared/` |
| **Replaces** | reading raw `/excursions` JSON, which nothing currently narrates |
| **Output** | "Fridge B is 2 °C over range; 250 ampoules of Propofol are in it" |
| **Guardrail** | Reports the breach, never decides the stock is unusable. |

### PH-4 · New-NDC due diligence — **Tier B**

> "What do we know about 00069-4061 before I add it?"

| | |
|---|---|
| **Joins** | openFDA NDC directory + `import_alert` + `warning_letter` + `news_signal`, already assembled by one function |
| **Blocked on** | `explore()` lives in `services/compliance/app/explore.py`, not `shared/` |
| **Replaces** | four separate lookups against three feeds |
| **Guardrail** | Spends openFDA budget — correctly gated behind `certification:explore`, which only pharmacist and admin hold. |

---

## 3. Doctor (`physician`)

Point-of-order consumer. Already holds all three existing tools' permissions, plus `patient:read`
and `patient:write` — which the pharmacist does not.

### DOC-1 · Prescribe-time combined check — **Tier B**

> "Can I put X on this patient's chart right now?"

The single highest-value workflow in this document, because it answers the safety question and
the availability question together — today they live on different screens and nothing joins them.

| | |
|---|---|
| **Joins** | patient vector → rules assessment → ingredient avoid-warnings → on-hand stock |
| **Reusable** | `patient_row_to_vector`, `avoided_ingredient_warnings`, the `Assessment` machinery are all already in [`shared/patient.py`](../shared/medstock_shared/patient.py) |
| **Blocked on** | the orchestration that `POST /cart-check` performs lives in `services/patient-profiling/app/main.py` |
| **Output** | Safety verdict with findings **and** whether it is physically in the building |
| **Guardrail** | Warnings only. The verdict is the rules engine's arithmetic, never the model's opinion. |

### DOC-2 · Blocked-drug fallback — **Tier A**

> "Then what can I give instead?"

Natural follow-up to DOC-1 in the same conversation. When the assessment flags a drug or stock is
zero, `search_analogues_rxnorm` (already ranked by this hospital's on-hand) answers immediately —
no second manual search, and the context carries over from the previous turn.

### DOC-3 · Plain-language verdict explanation — **Tier B**

> "Why was that flagged?"

`GET /explain/{request_id}` already returns per-factor contributions — deliberately deterministic
arithmetic, which is exactly what makes it safe to paraphrase. The model restates already-computed,
already-cited numbers as a sentence. It must add nothing: any number in the narrative that is not
in the source payload invalidates the whole narrative.

### DOC-4 · Patient regimen snapshot — **Tier A**

> "What is this patient already on?"

A direct read of the `patient` row (`patient:read`) summarised in prose, so the physician can
open the copilot instead of a second tab before asking DOC-1.

---

## 4. Clinical Director (`director`)

Oversight. Holds both forecast permissions and `audit:read` / `dashboard:read` — the latter two
backed by nothing.

### DR-1 · Cross-facility risk digest — **Tier B**

> "What needs my attention across all sites this morning?"

The clearest "one button replaces a morning of tab-switching" case in the system.

| | |
|---|---|
| **Joins** | at-risk SKUs (`forecast:read`) + storage excursions (`facility:read`) + red certificates (`certificate:read`) |
| **Blocked on** | the at-risk assembly is in `services/prediction/app/main.py`; the excursion query is in `services/warehouse/app/main.py`. The forecast *maths* is already shared (`shared/forecasting.py`), only the DB assembly is not. |
| **Output** | One ranked paragraph per facility, worst first |

### DR-2 · Run a forecast and narrate the delta — **Tier B**

> "Re-run the forecast and tell me what changed."

The director holds `forecast:run` (`POST /forecast/runs`). Today, triggering a run and then
finding out what it changed are two separate manual steps. This makes them one — and the "what
changed" half is precisely what a language model is good at and a table is bad at.

**Note:** a write action. It creates a forecast run row. It is safe to expose because it is
idempotent per day (same-day re-runs replace) and touches no outside party — unlike a purchase
order, which is why nothing resembling `place_order` appears anywhere in this document.

### DR-3 · Review-queue digest — **Tier B**

> "What is waiting on a pharmacist, and is any of it urgent?"

`GET /risk-profiles` under `profile:review`, which the director holds, summarised rather than
opened card by card.

### DR-4 · AI decision audit query — **Tier C**

> "Show me every AI-assisted decision on this drug last month."

`ai_audit_log` is written on every copilot turn — actor, request id, tools called, outcome,
latency — and **is never read back by anything.** `assessment_log` has the same property. The
`audit:read` permission exists for this and guards nothing.

This is the one workflow here that is mostly backend work, and it is also the one that makes the
provenance story real rather than theoretical.

---

## 5. Procurement Officer (`admin`)

Buys stock, owns suppliers. Note there is **no orders/procurement service in this repo** — the
`/orders` page is driven by client-side mock data. Everything genuinely available to this role is
compliance- and inventory-shaped.

### PR-1 · Formulary-wide certificate sweep — **Tier A**

Same tool as PH-2, no changes: `admin` holds both `inventory:read` and `certificate:read`. Read
here as a purchasing signal — do not reorder what is about to be recalled.

### PR-2 · New-NDC due diligence before sourcing — **Tier B**

Same tool as PH-4 (`admin` holds `certification:explore`), plus `check_stock_by_ndc` to catch the
common case of already stocking the same drug under a different package NDC.

### PR-3 · Storage-compliance report — **Tier B**

Same excursion data as PH-3, read for a different purpose: which sites cannot safely receive a
cold-chain delivery.

### PR-4 · Restock brief — **Blocked on a PERMS change**

The workflow procurement actually wants — "what should I buy this week, and is it compliant?" —
cannot be built, because `admin` has no `forecast:read`. The tool would be declared to every role
except the one that needs it.

Resolving this is a product decision, not an implementation one: either grant `forecast:read` to
`admin`, or accept that procurement asks a director. It is listed here because leaving it
unstated would make the role look better served than it is.

---

## 6. Summary

| ID | Role | Workflow | Tier | New tools |
|---|---|---|---|---|
| PH-1 | Pharmacist | Shortage response brief | **A** | 0 — quick action only |
| PH-2 | Pharmacist | Formulary-wide cert sweep | **A** | 1 |
| PH-3 | Pharmacist | Cold-chain excursion digest | B | 1 + promotion |
| PH-4 | Pharmacist | New-NDC due diligence | B | 1 + promotion |
| DOC-1 | Doctor | Prescribe-time combined check | B | 1 + promotion |
| DOC-2 | Doctor | Blocked-drug fallback | **A** | 0 |
| DOC-3 | Doctor | Plain-language verdict | B | 1 + promotion |
| DOC-4 | Doctor | Patient regimen snapshot | **A** | 1 |
| DR-1 | Director | Cross-facility risk digest | B | 1 + 2 promotions |
| DR-2 | Director | Run forecast, narrate delta | B | 1 + promotion |
| DR-3 | Director | Review-queue digest | B | 1 + promotion |
| DR-4 | Director | AI decision audit query | **C** | 1 + new read |
| PR-1 | Procurement | Cert sweep (shared with PH-2) | **A** | 0 |
| PR-2 | Procurement | New-NDC due diligence | B | 0 (shares PH-4) |
| PR-3 | Procurement | Storage-compliance report | B | 0 (shares PH-3) |
| PR-4 | Procurement | Restock brief | — | blocked on `forecast:read` |

Five workflows are Tier A. Two of those need no new tool code at all.

## 7. Design rules these workflows follow

1. **One tool, one permission.** A composite tool bound to a single permission that internally
   reads across several domains would silently widen access. Cross-domain workflows are built by
   letting the model chain single-permission tools, so RBAC stays exactly as `PERMS` defines it.
2. **The model never commits.** No tool writes to an outside party. DR-2 is the only write and it
   is internal and idempotent.
3. **Degradation removes convenience, never safety.** Every deterministic path — certification
   rules, the RxNorm graph, stock queries, the assessment engine — must keep working with Gemini
   switched off.
4. **No number without a source.** The model may narrate figures that appear in a tool result. It
   may not compute one.
