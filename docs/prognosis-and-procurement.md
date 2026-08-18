# PP-3 Prognosis · PP-4 Cohort-Aware Procurement

Two additions: an AI-derived prediction of how a drug is likely to affect a
particular patient, and a purchasing forecast that changes with who the patients
actually are.

Status: **design**. §5 lists what each one costs before it can be built.

---

## 0. The problem with "ask the model about the patient"

The obvious build is: send the patient to Gemini, ask what will happen. It is
also the one that fails every test that matters.

| | Sending the patient to a model | What is proposed here |
|---|---|---|
| Reproducible | no — same patient, different answer next week | yes, bit for bit |
| Reviewable basis | a paragraph of prose | a quoted FDA sentence |
| PHI exposure | clinical data into a globally-shared `ai_cache` | the model never sees a patient |
| Cache hit rate | ~0 — every patient is unique | ~100% — keyed per drug |
| FDA CDS exemption | at risk (§520(o)(1)(E)(d)) | intact |
| Cost | one call per assessment | one call per drug, ever |

**So the model predicts the population, not the person.** It reads the label and
extracts *which patient characteristics raise the risk of which reaction*. That
structured result is reviewed once, stored, and then matched against a patient
deterministically at request time.

The prognosis is genuinely AI-derived — the clinical knowledge comes out of prose
no rule could parse. The per-patient answer is arithmetic.

---

## 1. PP-3 — prognosis

### 1.1 What the label already says

Verified live against metformin's SPL:

> *"Risk factors for metformin-associated lactic acidosis include **renal
> impairment**, concomitant use of certain drugs, **age 65 years old or
> greater**, … hypoxic states (e.g., **acute congestive heart failure**),
> excessive alcohol intake, and **hepatic impairment**."*

Four of those are fields in the feature vector already (`egfr_band`, `age_band`,
`condition_codes`, `hepatic`). One label, 18+ statements of this shape. This is
a conditional risk model, published by the FDA, written as a sentence.

### 1.2 The task

Registered in `ai_tasks.py`, run **offline from `ingest`**, never on a request:

```
input   drug label sections: boxed_warning, warnings_and_cautions,
        use_in_specific_populations, contraindications
output  a risk profile, below
```

```json
{
  "rxcui": "861007",
  "risks": [
    {
      "reaction": "lactic acidosis",
      "seriousness": "fatal",
      "risk_factors": [
        {"feature": "egfr_band",       "op": "at_or_below", "value": "45-59"},
        {"feature": "age_band",        "op": "in",   "value": ["65-74","75-89","90+"]},
        {"feature": "hepatic",         "op": "eq",   "value": "impaired"},
        {"feature": "condition_codes", "op": "has",  "value": "I50.9"}
      ],
      "citation": "Risk factors for metformin-associated lactic acidosis include renal impairment, …",
      "section": "boxed_warning"
    }
  ]
}
```

### 1.3 What stops it being a hallucination generator

Four gates, in order. A profile failing any of them never reaches a pharmacist.

1. **Closed vocabulary.** `feature` must be one of the nine vector fields and
   `value` must be inside that field's enum. A model inventing `"feature":
   "smoking_status"` is rejected mechanically — we do not collect it, so we
   could never evaluate it.
2. **Verbatim citation.** `citation` must be a substring of the label section it
   claims. This is the rule [services.md](services.md) §6 already applies to
   `analogue`, and it is checkable without judgement.
3. **Human approval.** Profiles land `awaiting_approval`. A pharmacist accepts or
   rejects each one. Nothing colours a screen before that.

   `GET /risk-profiles` is the queue and `POST /risk-profiles/{id}/review` is the
   ruling, behind `profile:approve` — a permission held by the pharmacist role
   and by no other, admin included. Re-ruling is allowed, so an approval can be
   withdrawn when a label changes without anyone editing the database by hand.

   The gate was specified here before it was built: for its first two weeks the
   extraction job wrote `awaiting_approval` and **nothing in the system could
   write any other value**, so every profile ever extracted was unreachable and
   the two features reading the table saw it as empty. It failed silently in
   both directions — no error, and no profile.
4. **Versioned.** Each profile records the label `spl_id` and extraction date, so
   a prediction made today can still be explained after the label changes.

**One reaction, several placements.** A label routinely states the same risk more
than once: metformin's lactic acidosis is in the boxed warning *and* twice in
warnings and cautions, and the extraction faithfully returns all three. Both
naive handlings are wrong — three rows make stage 7b score one reaction three
times, and letting the `(rxcui, reaction)` key collapse them discards two at
random, which is what happened the first time this ran against a real label (the
boxed warning was the one lost, leaving a single risk factor where the label
gave three). `merge_by_reaction` unions the factors, keeps the quote from the
most authoritative section, and takes the gravest seriousness anyone assigned.

### 1.4 Applying it — still deterministic

At request time this is a lookup and a count, in the existing pipeline:

```
stage 7b  for each approved risk profile of this drug:
            matched = [f for f in risk_factors if patient matches f]
            if matched:
              Finding(
                code="PROGNOSIS_RISK",
                weight = base_weight(seriousness) * len(matched) / len(risk_factors),
                message=f"{len(matched)} of {len(risk_factors)} label risk factors "
                        f"present for {reaction}: {', '.join(matched)}",
                source=f"FDA label, {section}")
```

What a pharmacist sees:

> **Metformin** — elevated risk of *lactic acidosis*.
> 3 of 4 label risk factors present: eGFR 30-44, age 75-89, heart failure.
> *"Risk factors for metformin-associated lactic acidosis include renal
> impairment… age 65 years old or greater… and hepatic impairment."*
> — FDA label, boxed warning

That is a prognosis with a citation, reproducible, and arguable. The model
produced the knowledge; nothing about this patient was sent anywhere.

### 1.5 Where it does *not* apply

Prognosis raises a score. It never blocks. Hard gates stay deterministic and
absolute — an allergy is not a probability, and a model must not be able to
create or clear one.

---

## 2. PP-4 — procurement that knows who the patients are

### 2.1 The idea

Two hospitals with the same headcount and the same current prescriptions still
need different stock, because their patients differ. A cohort that is old and
renally impaired will migrate off metformin over the next quarter. A young one
will not. Purchasing from current prescriptions alone buys for the past.

`plan_demand` already answers *"what are they on, and what is safe"*. PP-4 adds
*"and where is that going".*

### 2.2 The four numbers, per drug

| Number | Meaning | Where it comes from |
|---|---|---|
| `on_therapy` | patients taking it now | built |
| `at_risk` | of those, how many their own profile flags | PP-3 + the existing rules |
| `switch_in` | patients likely to arrive from a drug they are at risk on | in-class substitution, already built |
| `cohort_fit` | share of the whole cohort who could ever take it | `eligible / cohort_size`, already computed |

```
projected = on_therapy − (at_risk × switch_rate) + switch_in
```

`switch_rate` is an **assumption, not a fact** — the share of flagged patients a
pharmacist actually switches. It is a single configurable number, published in
`/ruleset` like every other, and it must be labelled as an assumption wherever
the forecast is shown. Pretending it is measured would be the one dishonest
thing in this design.

### 2.3 What it tells a director that headcount does not

- **A drug most of the cohort cannot take is a bad stocking bet**, even if
  current usage looks healthy. `cohort_fit` says so directly.
- **Substitution pressure is visible before it happens.** 40 patients on a drug
  that is amber for 30 of them is a re-order decision this quarter, not a
  surprise next.
- **A shortage becomes a number.** COMP-1 marks a drug unavailable; PP-4 says
  which patients move where, and how many have nowhere to go — that last count
  is already returned as `unservable`.

### 2.4 Endpoint

```
POST /forecast
{
  "cohort": [...vectors...],
  "candidates": [...],
  "on_hand": {...},
  "unavailable": [...],
  "horizon_days": 90,
  "switch_rate": 0.6
}
```

Returns the `plan_demand` lines plus `at_risk`, `switch_in`, `cohort_fit`,
`projected_units`, and `assumptions` — echoing back every number that was
assumed rather than derived.

---

## 3. How the two connect

```
FDA label prose
   │  ask_ai("prognosis")            ← offline, in ingest, per drug
   ▼
risk profile  →  pharmacist approves  →  drug_risk_profile
   │
   ├──▶ PP-1  one patient   : "3 of 4 risk factors present"
   └──▶ PP-4  whole cohort  : "30 of 40 flagged → buy the alternative"
```

One extraction serves both. The per-patient answer and the purchasing forecast
come from the same approved table, so they can never disagree.

---

## 4. Schema

| Table | Class | Key columns |
|---|---|---|
| `drug_risk_profile` | reference | `rxcui` · `reaction` · `seriousness` · `risk_factors` jsonb · `citation` · `section` · `spl_id` · `status` · `reviewed_by` · `reviewed_at` · `review_note` · `extracted_at` |
| `prognosis_assumption` | reference | `name` · `value` · `note` — `switch_rate` lives here, not in code |

No new tenant tables and no patient storage: PP-3 is drug-level, PP-4 aggregates
vectors that arrive in the request and are discarded with it.

`reviewed_by` rather than `approved_by` because a rejection has a reviewer too,
and a rejecter's name in a column called `approved_by` reads as an approval a
year later. `reviewed_at` is separate from `extracted_at` because they answer
different questions — and `extracted_at` deliberately carries no `onupdate`,
since an ORM update would otherwise restamp the extraction date every time a
pharmacist ruled on the row, quietly breaking the versioning gate 4 depends on.

---

## 5. `OPEN` — what this costs

1. **`ingest` becomes an AI caller.** [services.md](services.md) §3 says only
   `analogue` and `prediction` call `ask_ai()`. This needs *"and `ingest`'s
   offline CronJobs"*. That is a smaller amendment than making a request-serving
   service an AI consumer — no request ever waits on a model, and no patient data
   is anywhere near it. **Recommended.**
2. **A pharmacist must review the profiles.** Gate 3 is not optional and it is
   not engineering work. Without a clinician signing off the extracted risk
   factors, this is a model's opinion in a table. The queue and the ruling
   endpoint exist now (§1.3), which means the review *can* happen — it has not.
   Building the gate is not passing through it, and until a clinician has ruled
   on them, `approved_profiles()` legitimately returns nothing.
3. **`switch_rate` has no empirical basis.** State it as an assumption on the
   screen, or the forecast will be read as a measurement.
4. **Extraction quality is unmeasured.** Before trusting it: extract 20 drugs,
   have them reviewed, and report the accept rate. If it is below ~80%, the
   prompt needs work before the feature does. `GET /risk-profiles` now returns
   `accept_rate` alongside the queue, so the number falls out of the review
   rather than needing a separate exercise. It is `null`, not `0.0`, until
   somebody has ruled on something — a rate over no decisions is unknown, and
   printing it as zero would fail the ~80% test on an untouched queue.
5. **Label volume.** 261,732 labels, 1.8 GB bulk. Extract only for the formulary
   — a few hundred drugs — not the corpus.

---

## 6. Why this answers the AI question properly

The project's AI story was thin: one registered task, and both compliance and
patient-profiling deliberately model-free. This adds the two places where a model
is doing work nothing else can:

- **Recall identity** (COMP-2, measured): a regex tops out at 40% of unjoinable
  recalls and mistakes ZIP codes for NDCs.
- **Conditional risk** (PP-3, above): the risk factors are in the label as
  English sentences, and there is no parser for English.

Both produce structured output, verified against a source, approved by a human,
and applied deterministically. That is the pattern the brief argues for —
grounding, explainability, human approval, audit — and it is now the pattern in
the places where AI is actually load-bearing rather than decorative.
