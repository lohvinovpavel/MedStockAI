# AI Workflow Implementation Plan

Engineering plan for the workflows in [ai_workflows.md](ai_workflows.md). Verified against the
source at commit `22d0489`.

Each workflow below states: the tool signature, where its data actually comes from, what has to
move before it can be written, the tests, and an effort estimate. Prerequisites shared by several
workflows are described once in §2 and referenced by id.

---

## 1. Constraints that shape every plan below

These are properties of the current codebase, not preferences. Ignoring any of them produces a
plan that cannot be built.

### 1.1 The copilot can only import `medstock_shared`

`services/analogue/pyproject.toml` declares exactly four dependencies:
`medstock-shared`, `fastapi`, `uvicorn`, `httpx`. It cannot import
`services/prediction/app/`, `services/compliance/app/`, or `services/warehouse/app/`.

**No service in this repo calls another over HTTP.** Every `httpx` call in `services/` targets an
external API (openFDA, RxNav). There is no internal service client, no service-discovery config,
and adding one would be a much larger change than moving a function.

Therefore a tool gets its data from exactly two places:

- a module under `shared/medstock_shared/`, or
- a direct DB query it issues itself.

Anything else is Tier B and needs a promotion from §2 first.

### 1.2 The tool contract

From [`registry.py`](../shared/medstock_shared/ai/tools/registry.py):

```python
@tool(permission="<one key from PERMS>", description="<what the model reads>", args=ArgsModel)
def tool_name(args: ArgsModel, principal: Principal) -> dict: ...
```

- **Sync, not async.** `execute()` runs it via `run_in_threadpool`; the copilot route stays async.
- **Exactly one permission.** `declarations_for()` hides the tool from roles without it and
  `execute()` re-checks before running. Binding a cross-domain tool to one permission would widen
  access silently — build cross-domain workflows by letting the model chain single-permission
  tools instead (`_MAX_TOOL_ROUNDS = 6` allows six rounds per turn).
- **Registration is an import side effect.** A new module under `tools/` must be imported in
  [`tools/__init__.py`](../shared/medstock_shared/ai/tools/__init__.py) or it silently does not exist.
- **Return a plain JSON-serialisable dict.** It becomes a `types.Part.from_function_response`.

### 1.3 Tenant scoping

- Tenant tables (`stock_snapshot`, `patient`, `assessment_log`, forecast rows) → `session_scope(principal.hospital_id, principal.user_id)`, which sets the RLS GUCs.
- Reference tables with no `hospital_id` (`drug_certification`, `certification_finding`, `drug`,
  `import_alert`, `warning_letter`, `news_signal`) → plain `Session(engine)`. `verify_batch_cert`
  documents this distinction and is the pattern to copy.

### 1.4 The test gotcha that will bite

[`services/analogue/tests/test_ai_copilot.py`](../services/analogue/tests/test_ai_copilot.py)
contains **three hardcoded tool-name sets** that must be updated on every tool addition:

- `test_declarations_are_scoped_to_the_caller_role`
- `test_denied_tools_for_is_the_complement_of_declarations_for`
- `test_system_prompt_names_role_gated_tools_the_model_may_not_call`

Adding a tool without touching these fails the suite. The unit-test pattern for a tool itself is
a `@contextmanager` fake over `session_scope` yielding a `MagicMock` whose
`.execute.return_value.all.return_value` is a list of row tuples — see
`test_check_stock_by_ndc_sums_across_locations`.

### 1.5 Audit is already wired

Every copilot turn writes one `ai_audit_log` row via `_write_copilot_audit`, including
`tools_called`. New tools inherit this — no per-tool audit code needed.

---

## 2. Shared prerequisites (promotions)

Four pieces of logic need to move into `shared/` before the Tier B workflows can be built. Each is
listed with its real cost, measured from imports rather than guessed.

### P1 · Promote `explore()` → `shared/medstock_shared/explore.py` — **small**

**Source:** [`services/compliance/app/explore.py`](../services/compliance/app/explore.py), 262 lines.

**Cost is almost zero.** Its imports are `medstock_shared.certification`,
`medstock_shared.models`, `medstock_shared.ndc_status`, plus stdlib/httpx/sqlalchemy. **No
service-local imports at all.** This is a file move plus updating one import line in
`services/compliance/app/main.py`.

`explore(session: Session, ndc: str) -> dict` already takes a session, so it composes cleanly.

**Caution:** `explore()` performs a live openFDA fetch. openFDA's budget is per-IP and shared
across every feed including the nightly CronJobs, which is exactly why it sits behind
`certification:explore` rather than `certificate:read`. Keep that binding.

**Unblocks:** PH-4, PR-2.

### P2 · Extract the excursion query → `shared/medstock_shared/warehouse.py` — **medium**

**Source:** `get_excursions` in [`services/warehouse/app/main.py:237`](../services/warehouse/app/main.py).

The join (telemetry × stock placement × `drug` storage class, with `temp_breach` / `humidity_breach`
predicates) is written inline in the route body. Extract it as
`excursions(session, facility_id=None) -> list[dict]`; the route becomes a thin caller. Warehouse
imports only `medstock_shared` + fastapi + sqlalchemy, so nothing else follows it.

**Unblocks:** PH-3, PR-3, and one third of DR-1.

### P3 · Promote the forecast read path → `shared/medstock_shared/forecasting.py` — **large**

**Source:** `services/prediction/app/main.py` helpers `_trailing_means`, `_forecast_by_ndc`,
`_on_hand`, `_depletion_fields` (lines 89–155), plus `services/prediction/app/forecast.py` (107
lines) and `supply.py` (109 lines).

The forecast **maths** is already shared — `forecast_series` lives in
[`shared/forecasting.py`](../shared/medstock_shared/forecasting.py). What is service-local is the
DB assembly and the at-risk ranking. That is ~200 lines to move plus its tests.

This is the most expensive prerequisite in the document and gates the least-certain workflows.
Sequence it last, and only if DR-1/DR-2 are actually wanted.

**Alternative worth considering first:** a copilot-side `days_of_supply_estimate` that computes a
trailing mean from `consumption_daily` and divides on-hand by it — perhaps 25 lines against tables
the tool can already read, no promotion at all. It would not reproduce prediction's quantile bands,
and the two would drift. Only acceptable if the tool's description states plainly that it is an
estimate, not the stored forecast run.

**Unblocks:** DR-1 (partly), DR-2, PR-4.

### P4 · An audit read path — **medium, and it is backend work, not copilot work**

`ai_audit_log` and `assessment_log` are written and never read. `audit:read` guards nothing.

Needs a query function in `shared/` (filter by hospital, drug, actor, date range, outcome) and,
separately, an HTTP endpoint if the `/audit` page is ever to show it. The copilot tool only needs
the former.

**Unblocks:** DR-4.

---

## 3. Workflow implementations

### PH-1 · Shortage response brief — Tier A, **no tool code**

The three tools this needs already exist and Gemini can chain them within one turn. The only work
is frontend.

**Change:** add a fourth entry to `QUICK_ACTIONS` in
[`web/components/dashboard/CopilotDrawer.tsx`](../web/components/dashboard/CopilotDrawer.tsx).

**Important:** `runAction()` currently routes to `replyFor()`, which reads
`web/lib/mock-data.ts` (`inventoryFor`, `forecastFor`, `parLevel`, `suppliers`) — **the existing
quick actions are still mock-backed.** This new action must not follow that path. It should call
`send()`-style streaming with a composed prompt so it goes through `/copilot/chat` and real tools:

```ts
// Sends a composed question through the real copilot stream, not replyFor()'s mock path.
const SHORTAGE_BRIEF =
  "For the drug currently in context: report on-hand stock by location, then find " +
  "substitutes ranked by what we hold, then check the compliance status of the top " +
  "candidate. Say plainly if any step returns nothing.";
```

The focus context is already injected into outgoing turns (`streamReply`, added in `739da13`), so
the model knows which drug "in context" means.

**Tests:** none new server-side. A frontend assertion that the button streams rather than calling
`replyFor` is worth having if the web suite grows one.

**Effort:** ~1 hour.

---

### PH-2 / PR-1 · Formulary-wide certificate sweep — Tier A

**Tool:** `sweep_shelf_certificates`, permission `inventory:read`, new function in
`shared/medstock_shared/ai/tools/pharmacy.py`.

```python
class SweepShelfArgs(BaseModel):
    status_filter: str = Field(
        "attention",
        description="'attention' for red/yellow only (default), 'all' for every stocked NDC",
    )

@tool(
    permission="inventory:read",
    description=(
        "Review the compliance status of every NDC this hospital currently holds in stock. "
        "Use when the user asks what on the shelf needs attention, rather than about one drug."
    ),
    args=SweepShelfArgs,
)
def sweep_shelf_certificates(args: SweepShelfArgs, principal: Principal) -> dict: ...
```

**Algorithm:**

1. `session_scope(...)` → distinct `StockSnapshot.ndc` with summed quantity for the hospital.
2. Plain `Session(engine)` → `DrugCertification` + `CertificationFinding` for those NDCs
   (reference tables, no tenant column — same split `verify_batch_cert` documents).
3. `signal()` from `shared/certification.py` per NDC; stored `record.status` wins over the
   recomputed colour, matching both `verify_batch_cert` and compliance's `GET /status`.
4. Filter to non-green unless `status_filter == "all"`.

**Output:**

```python
{"checked": 81, "flagged": [
    {"ndc": "...", "status": "red", "quantity": 250, "codes": ["RECALL_CLASS_I"]},
], "unknown": ["..."]}
```

**Two things to get right:**

- **`unknown` is not clean.** NDCs with no `drug_certification` row go in their own list, never
  silently into the healthy majority.
- **Cap the result.** A hospital with thousands of NDCs would blow the model's context. Cap
  `flagged` at ~50 and return a `truncated: true` flag; `_KEEP_LIMIT = 5` in the same module is
  the existing precedent for capping tool output.

**Tests:** one unit test (fake `session_scope`, three NDCs — one red, one green, one unknown;
assert the green is absent, the unknown is in `unknown`, the red carries its codes) plus the three
tool-name-set updates from §1.4.

**Effort:** ~4 hours including tests.

---

### PH-3 / PR-3 · Cold-chain excursion digest — Tier B (needs **P2**)

**Tool:** `list_storage_excursions`, permission `facility:read`.

```python
class ExcursionArgs(BaseModel):
    facility_id: int | None = Field(None, description="Limit to one facility; omit for all")
```

After P2 the body is a call to the promoted `excursions(session, facility_id)` plus shaping.
Return the breach rows with the affected drug, location, measured value, and the allowed range —
the model narrates; it must not decide the stock is unusable.

**Cap** to the worst ~30 rows, ordered by magnitude of breach.

**Tests:** the promotion needs its own test in the warehouse suite (unchanged route behaviour),
plus one tool test.

**Effort:** P2 ~4 hours, tool ~2 hours.

---

### PH-4 / PR-2 · New-NDC due diligence — Tier B (needs **P1**)

**Tool:** `explore_ndc`, permission `certification:explore` — deliberately *not*
`certificate:read`, because this spends the shared openFDA budget.

```python
class ExploreNdcArgs(BaseModel):
    ndc: str = Field(description="NDC to research against FDA feeds and directory")
```

Body after P1 is close to:

```python
with Session(engine) as session:          # reference tables only, no tenant column
    return explore(session, args.ndc)
```

**Watch the payload size.** `explore()` returns import alerts, warning letters, news items and the
directory record. Trim to counts plus the top few items per category before returning, or one call
can dominate the turn's context.

**Tests:** monkeypatch the promoted `explore` and assert the tool passes the NDC through and
trims; the real openFDA fetch must never run in a test — `explore.py` already isolates the fetch
in `_directory_record`.

**Effort:** P1 ~2 hours, tool ~2 hours.

---

### DOC-1 · Prescribe-time combined check — Tier B

**The highest-value workflow, and the one needing the most care**, because it touches the
assessment path that keeps this system decision-*support* rather than a regulated device.

**Recommended shape: two tools the model chains, not one composite.**

| Tool | Permission | Reads |
|---|---|---|
| `assess_patient_for_drug` | `profile:assess` | `patient` row → `patient_row_to_vector` → findings |
| `check_stock_by_ndc` | `inventory:read` | already exists |

A single composite tool would have to bind to one permission while reading both patient and
inventory data — precisely the widening §1.2 forbids. Two tools keep RBAC exact and let the model
skip the stock lookup when the safety answer is already "no".

**What is reusable:** a great deal. [`shared/patient.py`](../shared/medstock_shared/patient.py)
already exports `patient_row_to_vector`, `avoided_ingredient_warnings`,
`profile_avoided_ingredients`, `prognosis_findings`, `adr_findings`, `pgx_findings`, and the
`Assessment` / `RiskProfile` types. **The rules engine is already shared.** What is service-local
is only the orchestration in `POST /cart-check`.

**Therefore the promotion is small:** extract the assemble-and-assess sequence from
`services/patient-profiling/app/main.py` into
`shared/medstock_shared/patient_assess.py::assess_for_drug(session, patient_id, rxcui) -> Assessment`,
and have both the route and the tool call it.

**Non-negotiable:** the verdict must remain the rules engine's arithmetic. The model reports it
and never overrides, softens, or recomputes it. The existing system instruction ("answer only from
your tools' results") covers this, and the tool description should repeat it.

**Tests:** the promotion is covered by patient-profiling's existing suite if the route keeps its
behaviour; add one tool test with a fake session, and one asserting that a `blocked` verdict is
returned verbatim.

**Effort:** promotion ~6 hours, tool ~3 hours.

---

### DOC-2 · Blocked-drug fallback — Tier A, **no code**

`search_analogues_rxnorm` already ranks by this hospital's on-hand stock, and conversation history
carries the previous turn. This works the moment DOC-1 exists; it needs no tool and no button.

Worth one line in the tool description of DOC-1's tool telling the model that on a blocking
verdict, searching analogues is the useful next step.

**Effort:** 0.

---

### DOC-3 · Plain-language verdict explanation — Tier B

**Tool:** `explain_assessment`, permission `profile:explain`.

Reads the stored assessment by `request_id` and returns its factor contributions. The model
paraphrases.

**The validator is the whole design.** The contributions are deterministic arithmetic — that is
what makes them safe to restate and what a reviewer must be able to check independently. So:

> **Reject the narrative if it contains any number not present in the source payload.** Extract
> every digit sequence from the model's sentence and require each to appear among the source's
> score, weights, and shares. A wrong number sitting next to correct ones is worse than no
> sentence at all — reject the whole narrative, do not try to salvage part of it.

This check belongs on the response, not inside the tool: the tool returns data, the model writes
prose. Enforcing it means post-processing the copilot's text for this task, which the current SSE
loop does not do. **Simplest honest version for v1:** ship the tool (structured contributions,
model narrates) and accept the system-instruction guardrail; add the numeric validator when a
non-streaming narration path exists. Say so in the tool description rather than implying a
guarantee that is not enforced.

**Effort:** tool ~3 hours; the validator is a separate, larger piece of work.

---

### DOC-4 · Patient regimen snapshot — Tier A

**Tool:** `get_patient_regimen`, permission `patient:read` (held by physician and admin, **not**
pharmacist).

Direct read of the `patient` row under `session_scope`, returning active RxCUIs, allergies,
conditions, and PGx phenotypes.

**PHI boundary — the one real risk in this document.** `patient` is the documented PHI exception
and holds a name and date of birth. A tool result is sent to Gemini.

**Return the clinical vector, never the identifiers.** No `full_name`, no `date_of_birth` — an age
band via `age_band_from_dob` (already in `shared/patient.py`) if age matters. This mirrors the
de-identification the assessment path already performs and keeps the copilot outside the PHI
boundary.

**Tests:** one test asserting name and DOB are absent from the returned dict. That test is the
point of the workflow, not a formality.

**Effort:** ~3 hours.

---

### DR-1 · Cross-facility risk digest — Tier B (needs **P2** and **P3**)

Three tools chained, one per permission domain:

| Tool | Permission | Status |
|---|---|---|
| `list_at_risk_skus` | `forecast:read` | needs P3 |
| `list_storage_excursions` | `facility:read` | from PH-3 |
| `sweep_shelf_certificates` | `inventory:read` | from PH-2 |

No new orchestration: the model calls all three and writes the paragraph. Build PH-2 and PH-3
first and two thirds of DR-1 arrives for free.

**Effort:** P3 dominates; the tool itself is ~3 hours on top.

---

### DR-2 · Run a forecast and narrate the delta — Tier B (needs **P3**)

**The only write in this plan.** `create_run` in `services/prediction/app/main.py` fits and stores
a run; same-day re-runs replace, so it is idempotent per day and touches no outside party.

**Even so, do not make it a silent tool call.** Follow the existing HITL pattern: the tool returns
a *proposal* ("re-run the forecast for facility X — last run was 14:02 today") and a button
commits it, matching the rule that the copilot prepares and a human clicks. A model that can
re-run a forecast because a drug label it was summarising told it to is a prompt-injection path,
and this endpoint is reachable by two roles.

Narrating the delta needs the previous run retained for comparison — verify that same-day replace
does not discard what you want to diff against before promising this half.

**Effort:** P3, plus ~5 hours including the confirm-card frontend.

---

### DR-3 · Review-queue digest — Tier B

**Tool:** `list_review_queue`, permission `profile:review`.

`GET /risk-profiles` is one query in patient-profiling; extract it to `shared/` the same way as
DOC-1's promotion, then summarise. Return counts by verdict plus the top few by severity — not
every card.

Same PHI rule as DOC-4: verdicts and drugs, never names.

**Effort:** ~5 hours including the promotion.

---

### DR-4 · AI decision audit query — Tier C (needs **P4**)

**Tool:** `query_ai_decisions`, permission `audit:read`.

```python
class AuditQueryArgs(BaseModel):
    days: int = Field(30, description="Look-back window in days")
    task_type: str | None = Field(None, description="e.g. 'copilot', 'analogue'")
    outcome: str | None = Field(None, description="live | cache_hit | error | breaker_open")
```

Reads `ai_audit_log` scoped to the hospital, aggregated — counts by outcome, tools most called,
error rate, latency percentiles — plus a handful of recent rows. **Aggregate, do not dump:** a
month of turns is far more rows than a turn's context can hold.

`ai_audit_log` is append-only by grant, so the read is safe by construction.

**This is the workflow that turns the provenance design from written-down into usable.** It is
also the only one whose value is mostly outside the copilot — the same query backs an `/audit`
page.

**Effort:** P4 ~6 hours, tool ~3 hours.

---

### PR-4 · Restock brief — blocked

Requires granting `forecast:read` to `admin` in `PERMS`. One line of code; a product decision
about whether procurement should see clinical forecast data directly. Not an implementation task
until that is answered.

---

## 4. Sequencing

Ordered by value per unit of work, given that Tier A needs nothing and P3 is expensive.

| Phase | Contents | Effort | Delivers |
|---|---|---|---|
| **1** | PH-1 (quick action), PH-2/PR-1 (`sweep_shelf_certificates`) | ~1 day | Two roles get a real workflow; zero promotions |
| **2** | P1 → PH-4/PR-2 (`explore_ndc`); DOC-4 (`get_patient_regimen`) | ~1 day | Cheapest promotion in the repo; PHI boundary test lands early |
| **3** | P2 → PH-3/PR-3 (`list_storage_excursions`) | ~1 day | Completes pharmacist and procurement sets |
| **4** | DOC-1 promotion + `assess_patient_for_drug`; DOC-2 free; DOC-3 tool | ~2 days | The physician's highest-value workflow |
| **5** | P4 → DR-4 (`query_ai_decisions`); DR-3 | ~2 days | Makes `audit:read` mean something |
| **6** | P3 → DR-1, DR-2 | ~3 days | Only if the forecast digest is actually wanted |

After phase 3, every role except director has at least three working workflows and no expensive
promotion has been paid for.

## 5. Deliberately not built

| Not building | Why |
|---|---|
| A composite "do everything" tool per role | Would bind cross-domain reads to one permission (§1.2). Chain single-permission tools instead. |
| Any tool that places an order or approves a substitution | Irreversible and outside-world. `recommendation:approve` stays a human click; there is no orders service to call anyway. |
| An internal service-to-service HTTP client | Promotion into `shared/` is cheaper than inventing a transport, and §1.1 shows the pattern does not exist here yet. |
| Patient names or DOB in any tool result | The copilot stays outside the PHI boundary. Vectors and age bands only. |
| A duplicate forecast implementation in the copilot | Two forecast paths would drift. Either promote P3 or ship a clearly-labelled estimate — not a second silent source of truth. |
