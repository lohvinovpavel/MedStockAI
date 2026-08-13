# Demo Data

The system is headed for a hospital, so production data will be PHI. The demo is not. This
document is how we keep those two facts from ever meeting.

---

## 1. One rule

> **Reference data is real. Tenant data is synthetic. Nothing in between.**

That line is not a demo convenience — it is exactly the table split already in
[services.md](services.md) §1.1, so the demo exercises the real boundary rather than a fake one.

| Class | Tables | In the demo |
|---|---|---|
| **Reference** | `drug`, `shortage_event`, `drug_price`, `rxnorm_edge`, rule tables | **Real.** FDA, RxNorm and NADAC are public — there is no reason to fake them, and faking them would hide feed bugs until a hospital found them |
| **Tenant** | `formulary_item`, `stock_snapshot`, `recommendation`, `review_decision`, patient vectors | **Generated.** Never sourced from a real hospital |

Because synthetic patients are built on *real* RxCUIs and NDCs, every join, every interaction
lookup and every certification check behaves in the demo exactly as it will in production. The
only thing that isn't real is the people.

---

## 2. The rule that has no exceptions

**We never accept real patient data. Not even de-identified. Not even from a friendly contact at
a hospital who offers.**

This is worth stating flatly because it is how projects like this actually fail. The offer
arrives late, framed as helpful — *"here's a CSV, we stripped the names"* — and accepting it
means:

- Name-stripping is not de-identification. Safe Harbor has 18 identifier classes, and dates and
  rare diagnoses are the ones people miss.
- Without a signed BAA already in place, receiving it is a breach at the moment of receipt.
- It lands in someone's Downloads folder, then a test fixture, then git history, which is
  immutable and replicated to every clone.

If a hospital wants to help, the useful thing to ask for is their **formulary list and their CSV
column layout** — drug codes and file structure, no patients. That is the integration risk worth
retiring early, and it carries no PHI.

---

## 3. Generating patients

Use **Synthea** (MITRE, open source). It generates whole synthetic populations with realistic
disease progression, medication histories and lab values, and exports FHIR or CSV. It exists for
exactly this problem, and "we used MITRE's synthetic patient generator" is a better answer at
defense than "we wrote a random generator."

A thin adapter converts Synthea output into our feature vector:

```
Synthea patient ──▶ adapter ──▶ feature vector (patient-profiling-usecases.md §2.3)
                                age_band, egfr_band, allergy_codes,
                                condition_codes, active_rxcuis, prior_adr
```

That adapter is the same shape as the real de-identification gateway from
[phi-readiness.md](phi-readiness.md) §4 — so writing it is not throwaway demo work, it is the
reference implementation of a contract a hospital will later have to meet.

If Synthea proves heavy, a hand-rolled generator is acceptable with one condition: **correlate
the fields.** An 80-year-old with perfect kidney function and no medications is not a patient,
and a demo full of them makes the safety pipeline look like it does nothing. Age drives renal
function, conditions drive medications, medications drive interaction risk.

---

## 4. Design the scenarios, don't hope for them

Random data will not produce the cases the demo needs. Seed these deliberately:

| Scenario | Must demonstrate |
|---|---|
| Clean substitution | 🟢 green, cheaper alternative, approved in one click |
| Allergy block | Hard gate fires — **no score computed**, candidate excluded outright |
| Interaction warning | 🟡 amber ~35 points, warfarin clash, pharmacist decides |
| Renal dose limit | Kidney function excludes an otherwise ideal option |
| Expired certification | 🔴 red from `compliance`, dropped before safety ever runs |
| Unknown drug | ⚪ triggers the on-demand exploration path (UC-2) |
| Missing data | eGFR unknown → the result says the check was skipped, not "fine" |
| Nothing left | Every candidate excluded — the honest empty state |

The last two are the ones teams skip and examiners ask about.

**Fix the seed.** A demo that generates fresh patients each run will eventually produce a screen
nobody has seen before, live, on stage. `DEMO_SEED=42`, committed, reproducible.

---

## 5. Making it unmistakably fake

So that nobody — including us, in six months — can mistake demo data for real:

- Hospital name `DEMO GENERAL HOSPITAL`, id `demo-hospital-001`
- Every synthetic `patient_ref` prefixed `SYN-`
- Seeded through a migration or a `seed` CronJob that **refuses to run** unless
  `ENVIRONMENT=demo`
- A banner in `web` whenever the tenant is a demo tenant
- Demo tenants live in their own hospital row, so row-level security isolates them exactly as it
  isolates real ones

That last point matters: the demo is not a bypass of the tenancy model, it is a tenant. If RLS
is broken, the demo breaks too — which is the point.

---

## 6. The seed job

```
services/ingest/app/seed_demo.py     # refuses unless ENVIRONMENT=demo
```

Order matters, because tenant data references real reference data:

1. Run the real reference feeds first — `rxnorm`, `pricing`, `shortages`
2. Create the demo hospital and its users, one per role
3. Build a formulary from real NDCs actually present in `drug`
4. Generate stock levels and 90 days of usage history — `prediction` needs history to forecast
5. Generate the patient cohort, including the §4 scenarios
6. Assert the scenarios still fire — if reference data shifted and the allergy case stopped
   blocking, the seed fails loudly

Step 6 is what keeps the demo honest as the real feeds move underneath it.

---

## 7. What changes at rollout

Nothing in the pipeline. The demo and a real hospital differ in exactly three places:

| | Demo | Hospital |
|---|---|---|
| Who de-identifies | our Synthea adapter | the hospital's own gateway |
| Where the vector comes from | seed job | their EHR |
| Tenant row | `demo-hospital-001` | theirs |

Reference data, rules, scoring, audit and RLS are identical. That is the argument for building
the demo this way rather than the fast way — **the demo is the system, with one tenant whose
patients happen not to exist.**

---

## 8. And on the BAA

The agreement that matters is with whoever runs the **database, logs, backups and model
endpoint** — the cloud provider. Not the code host: source control must never hold PHI under any
agreement, because git history is immutable and replicated to every clone. See
[phi-readiness.md](phi-readiness.md) for the seams that make that switch cheap when it comes.
