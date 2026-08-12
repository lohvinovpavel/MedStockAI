# Use Cases — Catalog · Inventory · Analogs

Business use cases and service-boundary decisions for MedStockAI (Project SHIELD).  
No API, database, or infrastructure design in this document.

---

## Actors (MVP)

| Actor | Description |
|---|---|
| **Physician** | Searches availability, requests analogs, reviews enriched alternatives |
| **Pharmacist** | Checks own stock, finds analogs, proposes alternatives to a customer |
| **Orchestrator (Application)** | Sequences Catalog ? Inventory ? Analogs ? Inventory enrichment; applies policies |
| **External Analogs AI/API** | Returns alternative candidates with full/partial labels |
| **External pharmacy sources** | Supply pharmacy purchase/availability signals via Inventory |

**Out of MVP:** Patient self-service, clinical HITL approve/reject, EHR prescription write-back.

---

## Catalog

### UC-CAT-01 — Resolve medication identity

**Actor:** Physician, Pharmacist  
**Goal:** Turn a medication name into a confirmed `medicationId`.  
**Preconditions:** User authenticated.

**Main success flow**

1. User enters a medication name (brand / INN / partial).
2. Catalog returns candidate medications (internal ID + optional external aliases + display names per locale).
3. User confirms one candidate.
4. System proceeds with confirmed `medicationId`.

**Alternative / error flows**

- No candidates ? ask user to refine name; **do not** auto-start Analogs.
- Multiple candidates ? user must select one before availability/analogs auto-paths.
- Conversational extraction ambiguous ? ask clarifying question, then retry.

---

### UC-CAT-02 — View medication catalog status

**Actor:** Physician, Pharmacist  
**Goal:** Show catalog-level status (e.g. discontinued) when known.

**Main success flow**

1. System has a `medicationId`.
2. Catalog returns status (e.g. active / discontinued) and short explanation if available.
3. UI shows status alongside later Inventory/Analogs results.

**Alternative / error flows**

- Status unknown ? continue; manual Find analogs still allowed.
- **Discontinued + stock exists:**
  - **Clinical:** show stock + strong discontinued warning.
  - **Pharmacy retail:** may still sell if physically in stock.

---

## Clinical (Physician)

### UC-CL-01 — Structured search — medication availability

**Actor:** Physician  
**Goal:** Answer whether the patient can obtain the medication (clinic and/or allowed pharmacies).  
**Preconditions:** UC-CAT-01 completed.  
**Default scope:** Physician profile location + **allowed** inventory sources only.

**Main success flow**

1. Physician confirms `medicationId`.
2. Orchestrator calls Inventory.
3. Inventory returns **two result blocks**:
   - **Clinic stock:** `in_stock` | `low` | `unavailable` | `unknown` (+ quantity if permitted)
   - **Pharmacy purchase options:** where the medication can be bought among allowed pharmacies (status/availability only; **no raw external quantities**)
4. Physician reviews separated sections.

**Alternative / error flows**

- Clinic = `low` ? warning + CTA “Find analogs” (no auto).
- Clinic = `unavailable` ? offer / run UC-CL-04.
- Clinic = `unknown` or pharmacy sources fail ? show known data + explicit degraded/`unknown` for failed part.
- No permission for clinic quantity ? status without quantity.
- Discontinued in Catalog ? apply UC-CAT-02 clinical warning rules.

---

### UC-CL-02 — Conversational availability inquiry

**Actor:** Physician  
**Goal:** Same business outcome as UC-CL-01 via conversation.  
**Visibility policy:** Same as UC-CL-01.

**Main success flow**

1. Physician asks in natural language about medication availability.
2. Orchestrator extracts medication (and optional scope cues).
3. UC-CAT-01 confirms identity.
4. Inventory returns the same two blocks as UC-CL-01.
5. Assistant presents the result.

**Alternative / error flows**

- Unclear medication/scope ? clarifying question.
- Then same alternatives as UC-CL-01.

---

### UC-CL-03 — Find medication analogs (manual)

**Actor:** Physician  
**Goal:** Explicitly request possible alternatives (shortage, discontinued, patient request, etc.).  
**Preconditions:** Confirmed `medicationId` preferred (UC-CAT-01). Catalog status optional (UC-CAT-02).

**Main success flow**

1. Physician starts “Find analogs” for `medicationId`.
2. Orchestrator calls Analogs Service.
3. Analogs Service calls external AI/API and returns candidates with **full / partial** labels + explanation/source (passthrough from provider).
4. System shows disclaimer: informational recommendations for professional review — **not a prescription**.
5. Continue to UC-CL-05.

**Alternative / error flows**

- No confirmed ID ? UC-CAT-01 first.
- External AI/API failure or empty list ? show failure/empty; do not call Inventory for enrichment.
- Only partial analogs ? show with stronger caution.

---

### UC-CL-04 — Get analogs after unavailable (auto)

**Actor:** Physician (system-triggered)  
**Goal:** Automatically propose analogs when clinic stock is unavailable.  
**Trigger:** After UC-CL-01/02, clinic status = `unavailable` (not `low`, not “no catalog match”).

**Main success flow**

1. Clinic availability = `unavailable` for confirmed `medicationId`.
2. Orchestrator automatically invokes the same Analogs path as UC-CL-03.
3. Continue to UC-CL-05.

**Alternative / error flows**

- Physician dismisses auto-suggestion.
- Analogs failure ? same as UC-CL-03 errors.
- `low` does **not** trigger this UC (CTA only).

---

### UC-CL-05 — Review analogs with availability enrichment

**Actor:** Physician  
**Goal:** Review analogs together with availability.  
**Boundary rule:** Analogs Service does **not** call Inventory. Orchestrator enriches.

**Main success flow**

1. Orchestrator receives analog `medicationId`s (+ full/partial metadata).
2. Orchestrator calls Inventory in **one batch** for those IDs.
3. Enrichment prioritizes **full** analogs, then **partial** if time remains.
4. Total enrichment budget **? ~30 seconds**; unfinished items marked `unknown` / “not checked”.
5. Physician sees for each analog: type, explanation/source, clinic status (+ clinic qty if allowed), pharmacy purchase options (status only).
6. Physician may select another medication and repeat search/analog cycle.

**Alternative / error flows**

- Partial timeout ? show completed enrichments; mark the rest unknown.
- Empty analog list ? skip enrichment.
- Some sources forbidden for this physician ? omit them.

---

## Pharmacy retail (Pharmacist)

### UC-PH-01 — Check own-facility stock (structured)

**Actor:** Pharmacist  
**Goal:** Check whether the medication is available in the pharmacist’s own facility.  
**Default scope:** Profile location / own facility only (not full network).

**Main success flow**

1. Pharmacist resolves identity (UC-CAT-01).
2. Inventory returns own-facility status (+ quantity).
3. Pharmacist acts on the result.

**Alternative / error flows**

- `low` ? warning + CTA “Find analogs”.
- `unavailable` ? UC-PH-03.
- `unknown` ? show degraded state; allow retry / manual analogs.

---

### UC-PH-02 — Conversational stock inquiry

**Actor:** Pharmacist  
**Goal:** Same as UC-PH-01 via conversation.  
**Default policy:** Own facility only.

**Main success flow**

1. Pharmacist asks about stock in natural language.
2. Identity resolved (UC-CAT-01).
3. Inventory returns own-facility result.
4. Assistant presents it.

**Alternative / error flows**

- Ambiguous medication ? clarify.
- Explicit “search network” ? expand scope to network per org rules (see UC-PH-04).
- Then same status handling as UC-PH-01.

---

### UC-PH-03 — Find analogs when out of stock

**Actor:** Pharmacist  
**Goal:** Obtain alternative medications when own stock is missing or when manually requested.

**Triggers**

- **Auto:** own facility = `unavailable` after confirmed ID
- **Manual:** pharmacist starts Find analogs

**Main success flow**

1. Confirmed `medicationId`.
2. Orchestrator ? Analogs Service ? external AI/API.
3. Return full/partial candidates + explanation/source + retail-oriented disclaimer (suggestion to customer, not a clinical order).
4. Continue to UC-PH-04.

**Alternative / error flows**

- Same as UC-CL-03 for empty/failure cases.
- `low` ? no auto; CTA only.

---

### UC-PH-04 — Review analogs with stock check

**Actor:** Pharmacist  
**Goal:** See which analogs can be sold now from this pharmacy and/or found in the network.

**Main success flow**

1. Orchestrator gets analog IDs from Analogs Service.
2. Orchestrator batch-calls Inventory:
   - always enrich **own facility**;
   - include **network** during analog enrichment and/or when explicitly requested.
3. Prioritize full analogs, then partial; respect **? ~30s** budget.
4. Pharmacist sees: available here / available at network pharmacy Y / unavailable / unknown.
5. Own-facility quantities visible; network presentation follows org policy (at least location-level availability).

**Alternative / error flows**

- Network lookup fails ? still show own-facility enrichment.
- Timeout ? partial enrichment with unknown for the rest.
- Discontinued but in stock ? may still offer remaining packs (retail rule).

---

### UC-PH-05 — Propose alternative to customer

**Actor:** Pharmacist  
**Goal:** Use enriched analog information to propose a purchasable alternative to the customer.

**Main success flow**

1. Pharmacist selects an enriched analog from UC-PH-04.
2. System presents the information needed for the proposal (name, type full/partial, where available).
3. Pharmacist proposes it to the customer (human conversation).

**Alternative / error flows**

- Customer rejects ? pharmacist may pick another analog.
- Selected analog becomes unavailable ? refresh Inventory for that ID.

**Out of scope for this UC:** clinical prescribing, automatic substitution approval, patient self-service checkout.

---

## Cross-cutting rules

1. **Catalog** answers “What medication is this?”
2. **Inventory** answers “Do we have it and where?”
3. **Analogs** answers “What might be alternatives?” (recommendation only)
4. Services stay loosely coupled; **Orchestrator** owns sequencing and enrichment.
5. Auto-analogs only after **confirmed identity + `unavailable`**.
6. Allowed sources only (multi-tenant isolation).
7. System language/contracts: **English**; UI locale may differ.

---

## Service responsibilities (summary)

### Catalog Service

- Medication identity search and confirmation
- Internal `medicationId` + optional external code aliases
- Catalog status (e.g. discontinued)
- Does **not** own stock or analogs

### Inventory Service

- Answers: “Do we have this medication and where?”
- Internal facility stock + allowed external pharmacy sources
- Statuses: `in_stock` | `low` | `unavailable` | `unknown`
- Quantity/details by permission and source type
- Batch availability for a list of medication IDs
- Does **not** find analogs or resolve identity

### Medication Analogs Service

- Answers: “What medications could be alternatives?”
- Calls external AI/API
- Returns full vs partial (+ explanation/source)
- Recommendation-only; not a clinical decision
- Does **not** call Inventory or check stock

### Orchestrator / Application

- Use-case sequencing and auto-trigger policy
- Visibility policies (Clinical vs Pharmacy)
- Analog availability enrichment
- Conversational vs structured channels

---

## Dependencies

```
Physician/Pharmacist UI
        ?
        ?
 Orchestrator / App
   ?        ?         ?
   ?        ?         ?
Catalog   Inventory  Analogs ??? External AI/API
            ?
            ??? internal facility stock
            ??? external pharmacy APIs

Analogs ???? Inventory
Inventory ???? Analogs
Both ??? Catalog (medication ID / status read)
```

---

## Decision log (resolved)

| Topic | Decision |
|---|---|
| Service / contract language | English |
| UI language | Per user locale |
| Medication identity | Internal `medicationId` + optional external aliases |
| Current location | From physician/pharmacist profile |
| Inventory visibility | Only allowed sources (may include partner pharmacies) |
| Physician quantity | Clinic quantity allowed; external pharmacies = status only |
| Pharmacist quantity | Own facility quantity visible |
| `low` | Warning + CTA; no auto-analogs |
| Auto-analogs | Confirmed ID + `unavailable` only |
| Enrichment owner | Orchestrator (Analogs never calls Inventory) |
| Enrichment timeout | Up to ~30 seconds; partial/degraded preferred over hang |
| Enrichment strategy | One Inventory batch; prioritize full, then partial |
| Discontinued + in stock | Clinical: stock + strong warning; Retail: may still sell |
| Patient self-check | Not MVP |
| HITL clinical approval | Out of scope for these services |

### Still open (deferred)

- Exact `low` thresholds
- External pharmacy SKU ? `medicationId` matching rules
- Precise pharmacy network definition
- Whether full/partial labels are validated or passthrough-only
- Audit depth for conversational inquiries and PH-05 proposals
- Concrete N cap if enrichment must truncate under 30s

---

## Summary table: UC ? trigger ? services called ? output

| UC | Trigger | Services called | Output |
|---|---|---|---|
| **UC-CAT-01** Resolve medication identity | User enters medication name (structured or extracted from chat) | **Catalog** | Candidate list ? confirmed `medicationId` (+ aliases, display names) |
| **UC-CAT-02** View medication catalog status | `medicationId` known; status needed for context/warnings | **Catalog** | Catalog status (e.g. active/discontinued) + optional explanation |
| **UC-CL-01** Structured search — availability | Physician searches after confirmed ID | **Orchestrator** ? **Inventory** (profile location + allowed sources); may read **Catalog** status | Two blocks: clinic stock status/qty; pharmacy purchase options (status only) |
| **UC-CL-02** Conversational availability inquiry | Physician asks in natural language | **Orchestrator** ? **Catalog** (identity) ? **Inventory** | Same availability blocks as UC-CL-01, presented in conversation |
| **UC-CL-03** Find medication analogs (manual) | Physician explicitly requests analogs | **Orchestrator** ? **Analogs** ? External AI/API | Full/partial analog candidates + explanation/source + disclaimer |
| **UC-CL-04** Get analogs after unavailable (auto) | Clinic status = `unavailable` after CL-01/02 | **Orchestrator** ? **Analogs** ? External AI/API | Same as UC-CL-03 (auto-started) |
| **UC-CL-05** Review analogs with availability enrichment | Analog list available (from CL-03/04) | **Orchestrator** ? **Inventory** (batch; full first, then partial; ?~30s) | Analogs enriched with clinic + allowed pharmacy availability |
| **UC-PH-01** Check own-facility stock | Pharmacist structured stock check after confirmed ID | **Orchestrator** ? **Inventory** (own facility) | Own-facility status + quantity |
| **UC-PH-02** Conversational stock inquiry | Pharmacist asks in natural language | **Orchestrator** ? **Catalog** ? **Inventory** (own facility; network if explicitly requested) | Own-facility stock result (or expanded network if requested) |
| **UC-PH-03** Find analogs when out of stock | Auto on own `unavailable`, or manual Find analogs | **Orchestrator** ? **Analogs** ? External AI/API | Full/partial analogs + retail disclaimer |
| **UC-PH-04** Review analogs with stock check | Analog list available (from PH-03) | **Orchestrator** ? **Inventory** (own facility + network on enrichment/explicit request; batch; ?~30s) | Analogs enriched: available here / in network / unavailable / unknown |
| **UC-PH-05** Propose alternative to customer | Pharmacist selects enriched analog | Usually none new (uses PH-04 result); optional **Inventory** refresh for selected ID | Proposal info for customer conversation (name, type, where available) |
