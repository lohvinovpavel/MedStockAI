# Analog Search × Patient Profiling — End-to-End Flow

What happens between a pharmacist typing "find me an alternative" and a decision being written
to the audit log. Spans `analogue`, `compliance` and `patient-profiling`.

---

## 1. The journey

```mermaid
sequenceDiagram
    autonumber
    actor PH as 👤 Pharmacist
    participant W as Browser
    participant EHR as 🏥 Hospital EHR
    participant AN as ⚙ analogue
    participant CO as ⚙ compliance
    participant PP as ⚙ patient-profiling
    participant DB as 🗄 Postgres

    PH->>W: Drug X is in shortage — find an alternative
    W->>EHR: request patient context
    Note over EHR: de-identify at the edge<br/>strip name, MRN, exact dates
    EHR-->>W: feature vector, no PHI

    rect rgb(238, 244, 255)
    W->>AN: GET /analogues/rxcui
    Note over AN: no patient data ever<br/>reaches this service
    AN->>DB: walk rxnorm_edge, filter form and dose
    AN->>AN: ask_ai analogue — rank with citation
    AN-->>W: candidates A B C D
    end

    par certification check
        W->>CO: GET /status for A B C D
        CO->>DB: drug_certification lookup
        CO-->>W: A green · B yellow · C red · D unknown
        Note over CO: D unknown triggers COMP-2<br/>on-demand exploration
    and safety check
        W->>PP: POST /assess/batch — one vector, four candidates
        Note over PP: 13 deterministic stages<br/>run per candidate
        PP->>DB: rule tables, zero model calls
        PP-->>W: A green · B amber 35 · C green · D blocked
    end

    W->>W: join results — drop red and blocked
    W-->>PH: A recommended · B review required
    PH->>PP: POST decision — approve A
    PP->>DB: write review_decision
    Note over DB: trigger writes audit_log_entry<br/>append-only by grant
```

### Why the browser orchestrates

The three calls fan out from the browser, not from `analogue`. That follows
[services.md](services.md) §2 — the browser calls the services directly — but here it buys
something better than consistency:

**`analogue` never sees the patient.** It ranks by therapeutic equivalence and price, which is
all it needs. Clinical data reaches exactly one service, and it is the one designed around that
constraint. If `analogue` orchestrated instead, the patient vector would have to be handed to a
service that has no use for it and does call Gemini.

The `par` block is one round trip, not two — certification and safety are independent, so they
run concurrently. The vector is sent once, in a batch call, not once per candidate.

---

## 2. The funnel

Forty related concepts in RxNorm become one recommendation. Each stage removes candidates for a
different reason, and every removal is recorded.

```mermaid
flowchart TD
    S["💊 Drug X — in shortage"] --> E["Expand RxNorm graph"]
    E --> N1["~40 related concepts"]

    N1 --> F1{"Same route, form,<br/>strength class?"}
    F1 -->|no| X1["✂ dropped — 28"]
    F1 -->|yes| N2["12 candidates"]

    N2 --> F2{"In stock or<br/>orderable?"}
    F2 -->|no| X2["✂ dropped — 4"]
    F2 -->|yes| N3["8 candidates"]

    N3 --> F3{"Certification<br/>status"}
    F3 -->|🔴 expired or<br/>import-detained| X3["✂ dropped — 2"]
    F3 -->|🟢 🟡| N4["6 candidates"]

    N4 --> F4{"Patient hard gates<br/>pipeline stage 4"}
    F4 -->|allergy · absolute<br/>contraindication ·<br/>duplicate therapy| X4["✂ blocked — 2"]
    F4 -->|pass| N5["4 candidates"]

    N5 --> R["Rank: patient risk<br/>+ cert colour<br/>+ price delta<br/>+ days of supply"]
    R --> AI["🤖 ask_ai analogue<br/>rationale + citation"]
    AI --> OUT["📋 Ranked shortlist"]
    OUT --> PHARM["👤 Pharmacist<br/>approve · edit · reject"]
    PHARM --> AUD[("🔒 audit_log_entry")]

    classDef drop fill:#fdf0f0,stroke:#b05a5a,color:#3a1a1a
    classDef keep fill:#f0f7f0,stroke:#5a8a5a,color:#1a3a1a
    classDef gate fill:#fffaf0,stroke:#b08a4a,color:#3a2a1a
    classDef ai fill:#f4eeff,stroke:#7a5aa5,color:#2a1a3a
    class X1,X2,X3,X4 drop
    class N2,N3,N4,N5,OUT keep
    class F1,F2,F3,F4 gate
    class AI ai
```

**Order matters.** Cheap deterministic filters run before expensive ones, and the model runs
last — on four candidates, not forty. Certification is checked before patient safety because it
is a single indexed lookup, while the safety pipeline is thirteen stages.

**Nothing disappears silently.** A dropped candidate keeps its reason, and the UI can show
*"2 alternatives excluded: certification expired"*. A pharmacist who cannot see what was filtered
out cannot trust what remains.

---

## 3. Per-candidate detail

What stage 4 of the funnel actually runs, for one candidate:

```mermaid
flowchart LR
    IN["candidate B<br/>+ patient vector"] --> V["1-3 validate<br/>normalize · expand"]
    V --> G{"4 hard gates"}
    G -->|hit| B["🚫 BLOCKED<br/>no score computed"]
    G -->|pass| A1["5 interactions"]
    A1 --> A2["6 organ function"]
    A2 --> A3["7 FAERS signal"]
    A3 --> A4["8 pharmacogenomics"]
    A4 --> A5["9 age rules"]
    A5 --> SUM["10 weighted sum"]
    SUM --> BAND{"band"}
    BAND -->|0| GR["🟢 GREEN"]
    BAND -->|1-29| AM["🟡 AMBER"]
    BAND -->|30+| RD["🔴 RED"]

    classDef stop fill:#fdf0f0,stroke:#b05a5a,stroke-width:2px,color:#3a1a1a
    class G,B stop
```

Full stage table and weights: [patient-pipeline.md](patient-pipeline.md).

---

## 4. What the pharmacist sees

| Candidate | Cert | Patient risk | Price Δ | Verdict |
|---|---|---|---|---|
| **A** — ingredient equivalent | 🟢 active | 🟢 0 | −18% | ✅ Recommended |
| **B** — same class | 🟡 recall ongoing | 🟡 35 — moderate interaction with warfarin | −31% | ⚠ Review |
| ~~C~~ | 🔴 listing expired | — | — | Excluded |
| ~~D~~ | ⚪ unknown | 🚫 blocked — penicillin allergy | — | Excluded |

Both surviving rows carry a citation and the findings behind the colour. **Nothing on this screen
was decided by a model** — the ranking is arithmetic, the exclusions are rules, and the only
model output is the rationale text next to A.

The pharmacist approves. The trigger writes the audit entry. That row is the product's compliance
value, and it exists whether or not anyone ever reads it.
