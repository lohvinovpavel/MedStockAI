# Project SHIELD — Formulary & Supply Intelligence
### Product Brief

---

## Core Feature

**A hospital drug-inventory tracking and risk-forecasting service.**

| Component | What it does |
|---|---|
| Internal stock audit | Real-time stock level for every drug, per ward/pharmacy storage location |
| External distributor integration | Visibility into supplier stock and pricing before the hospital's own shelf runs out |
| Push risk notification | Explicit alert: "drug X will run out in N days at current usage" — before the shortage, not after |

This is the product's core — without it, nothing else has data to work on.

---

## Side Features (built around the core)

| Feature | Description |
|---|---|
| **Usage audit** | How much of which drug is consumed per day/ward; AI analytics on consumption trends and anomalies |
| **AI-ranked alternatives** | System proposes a cheaper equivalent with no loss of treatment quality, with justification and source citation |
| **Pharmacist HITL queue** | Every substitution is approved or rejected by a licensed pharmacist — the LLM never prescribes on its own |
| **Physician-facing tool** | The doctor sees a prompt at the point of ordering: "drug in shortage, here is the pharmacist-approved alternative" |
| *(extensible)* | Any further drug-related features — expiry tracking, P&T committee compliance reports, etc. |

---

## Personas (satisfies the capstone's multi-persona journey requirement)

```
Pharmacist  →  approves/rejects substitutions, works the review queue
Physician   →  gets a substitution prompt at the moment of ordering
Hospital (Pharmacy Director) → sees formulary-wide risk, savings, compliance
(optional) Patient → longer-term — transparency on "why was my drug substituted"
```

Each persona sees its own slice of the same underlying data — not four separate products, but four screens over one core.

---

## Business Value

| For whom | What they get | How it's measured |
|---|---|---|
| Hospital / Pharmacy Director | Never run out of a critical drug at the moment a patient needs it | Days of advance warning instead of an empty-shelf event |
| Hospital (finance) | Savings from switching to cheaper equivalents | Price delta × substitution volume per month |
| Pharmacist | No more hours spent manually searching for alternatives and calling suppliers | Manual hours removed from the workflow |
| Physician | Never prescribes a drug that's physically unavailable | Fewer cancelled/delayed orders |
| Compliance | Ready-made audit trail for Joint Commission / P&T reviews | Every decision and its rationale is already logged |

**One-sentence pitch:** "The system doesn't wait for a nurse to find an empty shelf — it warns days in advance, already paired with a pharmacist-approved, cheaper alternative."

The product can be sold three ways without changing the architecture — a standalone SaaS tool, an embedded service for a hospital system, or an extension to an existing pharmacy/EHR system. That's a go-to-market decision, not a product decision — it can be deferred.

---

## Why AI Is Actually Needed (not just CRUD)

1. **Drug matching is not a lookup.** The same active ingredient is interchangeable for one indication and dangerous for another (dose, form, comorbidities). This is reasoning over the RxNorm graph plus indication, not a static equivalence table.
2. **Shortage data arrives as unstructured text.** FDA/distributor feeds give free text, not a structured "substitute" field. The LLM extracts the fact and must cite the exact source sentence — otherwise the pharmacist has no way to verify it.
3. **Every supplier API is a different shape.** Instead of a hand-written adapter per distributor, the LLM builds the mapping schema into a single canonical stock model once, at integration time — a human confirms it once, and everything downstream runs deterministically, with zero LLM calls, fast and cheap.
4. **AI never decides on its own.** It ranks and proposes — only the pharmacist substitutes. Every recommendation and every human decision is written to an audit log. This is a direct requirement from the assignment (§4: explainability, grounding, human approval, audit logging), not just good practice.

Without AI, this would be a spreadsheet with manual formulas. With AI, it's a system that reasons over unstructured, heterogeneous data and explains every step it takes.
