# Data Provenance

Which numbers in this system are real, which are generated, and how to check.

Written because it is easy to lose track. A demo built on real reference data
and synthetic tenant data looks identical either way in a screenshot, and a
purchasing figure computed from invented patients is still a purchasing figure
on the screen. If someone asks "where did that number come from", this file is
the answer.

Counts and URLs verified 2026-08-14 against a locally migrated database and
live endpoints.

---

## 1. The rule

> **Reference data is real. Tenant data is generated. There is no third
> category, and nothing crosses.**

Same split as [services.md](services.md) §1.1, for the same reason: reference
data is public and identical for every hospital, tenant data belongs to a
customer we do not have yet.

---

## 2. Table by table

| Table | Rows | Origin | Real? |
|---|---:|---|---|
| `drug_price` | 20,000 | CMS NADAC, live fetch | **real** |
| `drug_certification` | 135 | openFDA NDC Directory, Enforcement, Shortages, RxNorm | **real** (7 rows deliberately seeded, marked `provenance='demo'`) |
| `certification_finding` | — | derived from the above by `medstock_shared.certification` | **real** |
| `drug_risk_profile` | 9 | Gemini extraction over real FDA label text | **real source, model-derived, `awaiting_approval`** |
| `rxnorm_edge` | — | RxNorm | **real** |
| `stock_snapshot` | 70 | `scripts/seed_stock.py`, `random.Random(42)` | **generated** |
| `formulary_item` | 4 | hardcoded list in the same script | **generated** |
| `hospital`, `app_user`, `membership` | 4 users | `services/auth/app/seed.py` | **generated** |
| patient feature vectors | 0 stored | `mock_ehr.py`, request-scoped only | **generated** |

---

## 3. What is real, and how to prove it

### Prices — CMS NADAC

What pharmacies pay per unit. Weekly, per 11-digit NDC, 2013–2026.

```
https://data.medicaid.gov/api/1/metastore/schemas/dataset/items
  -> title "NADAC (National Average Drug Acquisition Cost) <year>"
  -> https://data.medicaid.gov/api/1/datastore/query/{identifier}/0
```

Observed on the demo shelf: diflunisal 500 mg **$1.1730/EA** (generic), Lasix
20 mg **$0.7481/EA** (brand), amoxicillin 250 mg **$0.0665/EA**.

`pricing_unit` matters — `$1.04 per EA` and `per ML` are not comparable, and
`classification_for_rate_setting` (B/G) is what makes a brand-versus-generic
saving meaningful. Both are kept in `raw`.

### Certification — openFDA + RxNorm

`api.fda.gov/drug/ndc.json`, `/drug/enforcement.json`, `/drug/shortages.json`,
and `rxnav.nlm.nih.gov/REST/ndcstatus.json`. See
[compliance-usecases.md](compliance-usecases.md) §4.

### Risk profiles — real labels, model-extracted

Input is a public FDA label. Output is a model's structured reading of it, held
`awaiting_approval` until a pharmacist accepts it. **Real source, not yet a
clinical fact.** [prognosis-and-procurement.md](prognosis-and-procurement.md) §1.3.

---

## 4. What is generated — and the trap in it

`stock_snapshot` is the shelf. It is **70 rows from a seeded RNG**, attached to
real NDCs so every join behaves correctly. Nothing about the quantities is real.

That matters more than it looks, because **everything downstream of the shelf
inherits it**:

| Figure | Looks like | Actually is |
|---|---|---|
| "155 patients on metformin, 4,650 units" | a procurement plan | arithmetic over 500 invented patients |
| "shortfall 2,650 units" | a purchase decision | invented stock minus invented demand |
| "155 unservable" | a clinical risk | a property of the synthetic cohort |
| "27 of 52 shelf NDCs certified" | coverage | real certification of an invented shelf |

The cohort behind those is `mock_ehr.py` — a generator written for the
simulations. Correlated and plausible, and entirely fictional.

**The method is real and tested. The inputs are invented.** Both halves of that
sentence have to be said whenever these numbers are shown.

---

## 5. There is no procurement data, anywhere

Not in this repo, and not free.

| Source | What it is | Why it is not buying |
|---|---|---|
| **Medicare Part B** `Tot_Dsg_Unts` | dosage units administered, annual 2020–24, per HCPCS | billed administration, not a purchase order. Keyed on HCPCS, so it does not join to our NDC tables without a crosswalk |
| **Medicaid SDUD** `units_reimbursed` | quarterly per NDC, 1991–2026 | outpatient dispensing, Medicaid only. Joins for free — closest usable proxy |
| **NADAC** | price | no quantity at all |
| **IQVIA · Vizient · Premier · HealthTrust** | wholesaler shipments, GPO volumes | **the real answer, commercial licence** |
| **DEA ARCOS** | distribution to registrants incl. hospitals | public, but controlled substances only |

A trap worth recording: Part B shows filgrastim falling **26.1M → 10.2M** dosage
units between 2020 and 2024. That is not collapsing demand, it is biosimilar
substitution moving volume to a different HCPCS code. Read naively it says stop
buying a drug hospitals still use heavily.

**Real procurement data comes from the customer**, out of their ERP or
wholesaler feed. It is tenant data by definition, which is why none of it can be
sourced publicly and why `stock_snapshot` is generated.

---

## 6. Saying this out loud is the stronger position

The claim to make is not "we have hospital data". It is:

> Every reference feed is real and live — prices, certification, recalls,
> shortages, label-derived risk. The hospital-side data is synthetic because we
> have no hospital, and it is built on real drug codes so the day we do, only
> the source changes.

That is checkable in ten seconds against this file, and it is true.

---

## 7. Re-verifying

```bash
docker exec medstock-pg psql -U medstock -d medstock -c "SELECT provenance, count(*) FROM drug_certification GROUP BY 1;"
```

Anything `provenance='demo'` was seeded by `scripts/seed_certification.py` and
is deliberately fake. Anything `scheduled` or `on_demand` came from a live feed.

`drug_price`, `rxnorm_edge` and `drug_risk_profile` have no such column: they
are real by construction, because no script writes to them except the ingest
jobs.
