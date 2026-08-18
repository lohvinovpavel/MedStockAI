# AI Workflows — Interactivity Improvement Plan

Turning the copilot's answers from markdown prose into structured, actionable chat UI: rich result
cards per workflow, and human-in-the-loop confirmation for anything that writes.

Companion documents:

- [ai_workflows.md](ai_workflows.md) — original workflow design (stale; see fix plan F-16)
- [ai_workflows_fix_plan.md](ai_workflows_fix_plan.md) — defects found in the live audit
- [ai_workflows_migration_plan.md](ai_workflows_migration_plan.md) — LangGraph migration

> **This plan does not close any fix-plan item.** The HITL confirmation card makes a bad order
> *visible* before it is written; the server-side gates in F-03 and F-05 are what make it
> *impossible*. Ship both. A card is never a control.

---

## 1. Where we actually are

The audit found something better than a blank slate: **the card infrastructure already exists and
is simply not wired to the model.**

| Piece | State |
|---|---|
| `CopilotMessage.card` — JSONB column | Exists (`models.py:1304`), serialized by `_serialize_message` |
| `CopilotMessage.tool_name` | Exists, serialized, unused |
| `ResponseCard` TS union — `po \| analogues \| certificate \| emergency` | Exists in `CopilotDrawer.tsx:30` |
| `ResponseCardView` renderer | Exists, ~line 467, with copy-to-clipboard per card |
| `ToolActivity` pills (`tool_start` / `tool_end`) | Exists and works |
| `patientPicker` from `patient_disambiguation` | Exists and works |
| **LLM turns producing cards** | **Never.** `_persist_turn` writes `text` and `ai_dedupe_key` only |
| **SSE carrying a card frame** | **Never.** Events are `delta \| tool_start \| tool_end \| degraded \| patient_disambiguation \| done` |

So the system has two disconnected worlds:

- **Deterministic quick actions** — `replyFor()` in `CopilotDrawer.tsx` calls REST directly, builds
  a typed card client-side, and renders beautifully. No model involved.
- **The LLM chat path** — full tool access, real reasoning, multi-turn context, and output that is
  a wall of markdown.

Everything below is about collapsing those two worlds into one: **the model decides what to look
up; the tool result renders itself.**

There is a second reason to do this, from the audit. `tool_end` carries `{name, ok}` and nothing
else, so a tool's result never reaches the client. That is why fix-plan F-13 concludes the design
rule *"no number without a source"* is unverifiable from outside the process. A card frame **is**
that payload, structured and typed. One change, two problems.

---

## 2. The core mechanism

### 2.1 Cards are projections of tool results, not model output

The model never authors a card. A card is a pure function of a tool result that the caller's role
was already permitted to receive:

```
tool executes → result dict → card projector → SSE `tool_card` → persisted row → rendered component
                                    ↑
                          the model is not in this path
```

This matters for three reasons: no new permission surface (the projection sees only what the tool
returned); no hallucinated numbers (the model cannot edit a card); and the card survives when the
model is degraded, satisfying §7.3 of the original design — *degradation removes convenience,
never safety*.

### 2.2 Card contracts in Pydantic, TypeScript generated

Cards are a wire contract between Python and TypeScript. Define once, in
`shared/medstock_shared/ai/cards.py`:

```python
class CardBase(BaseModel):
    kind: str
    tool: str                       # which tool produced it
    request_id: str
    coverage: Coverage | None = None   # see §2.5


class Coverage(BaseModel):
    """Why an empty list is empty. Fix-plan F-08 in visual form."""
    checked: int
    total: int | None = None
    window: str | None = None
    source_note: str | None = None     # e.g. "no telemetry recorded for any location"


class AnaloguesCard(CardBase):
    kind: Literal["analogues"] = "analogues"
    query: DrugRef
    items: list[AnalogueRow]
    truncated: bool = False
```

Generate the TS union from the JSON Schema in CI (`datamodel-code-generator` → `json-schema-to-typescript`,
or `pydantic2ts`). A card kind added in Python without regenerating types fails the build. This is
the smallest thing that stops the two worlds drifting apart again.

### 2.3 The projector registry

Sits next to the tool registry and mirrors its shape:

```python
# shared/medstock_shared/ai/cards.py
_PROJECTORS: dict[str, Callable[[dict, Principal], CardBase | None]] = {}

def card_for(tool_name: str, result: dict, principal: Principal, request_id: str) -> CardBase | None:
    proj = _PROJECTORS.get(tool_name)
    if proj is None:
        return None                  # tools without a card degrade to prose. That is fine.
    try:
        return proj(result, principal)
    except Exception:                # a broken projector must never break the turn
        _log.exception("card projection failed tool=%s", tool_name)
        return None
```

A projector is registered per tool. Tools with no projector keep working exactly as today — this is
incrementally adoptable, one tool at a time, with no flag day.

### 2.4 SSE and persistence

In `_run_turn`, immediately after a successful `tool_end`:

```python
card = card_for(name, result, principal, request_id)
if card is not None:
    yield _sse("tool_card", {"name": name, "card": card.model_dump(mode="json")})
```

In `gateway._persist_turn`, capture `tool_card` frames the same way `delta` frames are captured
today, and write **one `CopilotMessage` row per card** using the columns that already exist:

```python
CopilotMessage(role="tool", tool_name=name, card=card_json, text=None, ...)
```

Ordered before the assistant's text row. On reload, `GET /conversations/{id}` replays cards for
free — `_serialize_message` already returns `card` and `tool_name`.

> **Schema note.** `card` is one JSONB column on one row, and a turn can produce several cards. Do
> not widen the column to a list — use one row per card. The `tool_name` column is already there
> and unused, which suggests this was the intent.

### 2.5 Coverage: making "nothing" look like nothing

Four times in the audit the copilot reported an empty table as a clean result, once green-lighting
a cold-chain delivery from zero telemetry. Prose is bad at this distinction; UI is good at it.

Every list-shaped card carries `Coverage`, and the renderer has **two distinct empty states**:

| Case | Visual |
|---|---|
| `checked > 0`, no rows | Green check, *"12 locations checked · all within range"* |
| `checked == 0` | Amber outline, *"No readings recorded — this is not a clean result"*, with the `source_note` |

The same component is reused by the at-risk card, the review-queue card and the sweep card. This is
the UI half of fix-plan F-08; the tool-side counts are the other half and must land first.

---

## 3. Human-in-the-loop: anything that writes goes through a button

### 3.1 The pattern

Today `draft_order` writes a row the moment the model calls it. The audit's F-05 finding is a
direct consequence: the model invented a required `review_decision_id` and nobody saw it happen.

Invert it:

```
model calls propose_order  →  read-only validation + a proposal object
                           →  ProposalCard rendered with Confirm / Adjust / Cancel
                           →  user presses Confirm
                           →  POST /api/inventory/orders  (normal REST, full server validation)
                           →  result card replaces the proposal card in place
```

The proposal tool **writes nothing**. It resolves the supplier, prices the line, fetches the
certification status and the cited review decision, and returns everything the human needs to
judge it. The write is an ordinary authenticated REST call made by the browser, subject to the same
server-side gates as any other order — including F-03's compliance block and F-05's decision-match
assertion.

### 3.2 The proposal card

```
┌─────────────────────────────────────────────────────────┐
│ DRAFT PURCHASE ORDER — needs your confirmation          │
├─────────────────────────────────────────────────────────┤
│ Heparin Sodium 5000 IU/mL        ● RED — Class I recall │
│ NDC 00338043304                    FDA shortage, ongoing │
│                                                          │
│ Quantity   5 000 units  (pack 100 → 50 packs)           │
│ Supplier   Meridian Pharma · lead time 6 d               │
│ Coverage   ~34 days at current burn                      │
│ Est. total $12 400                                       │
│                                                          │
│ ⚠ Cites review decision #1, which approves               │
│   Norepinephrine 4 mg/4 mL — not this drug.              │
│                                                          │
│ ⛔ Blocked: an NDC under an open Class I recall cannot   │
│    be ordered. Clear the compliance block first.         │
│                                                          │
│ [ Confirm order ]  [ Adjust… ]  [ Cancel ]               │
│   ↑ disabled              ↑ opens quantity/supplier      │
└─────────────────────────────────────────────────────────┘
```

Both audit failures become visible before anything is written: the mismatched review decision is
stated in plain language, and the recall blocks the primary action outright.

**Confirm is disabled, not hidden**, with the reason next to it. A hidden button teaches users
nothing; a disabled one with a reason teaches them the rule.

### 3.3 Safety properties

| Property | How |
|---|---|
| Server is the authority | Confirm calls REST; every gate re-runs there. A forged POST is rejected identically. |
| No double-write | The proposal carries a UUID `proposal_id`; the POST sends it as an idempotency key. Double-click, retry and reconnect all collapse to one order. |
| Proposals expire | 15 minutes. Prices, stock and certification status all move; a stale proposal must be re-validated, not replayed. |
| Confirm ≠ place | Confirming creates `status: draft`. Placing an order with a supplier remains outside this system entirely (original design §7.2). The button says **Confirm draft**, and the resulting card says `Draft · not sent to supplier`. |
| Audit | The confirm POST records `actor_id`, `proposal_id` and the originating `request_id`, so the chat turn and the order row are joinable — which is what fix-plan F-13 needs to make provenance real. |

### 3.4 What else becomes HITL

| Action | Permission | Card | Why a button |
|---|---|---|---|
| Draft purchase order | `order:write` | ProposalCard | F-03, F-05 |
| Trigger a forecast run | `forecast:run` | RunProposalCard | A write; today no tool does it at all (F-11). The button *is* the missing workflow. |
| Approve / reject a review-queue item | `profile:approve` | ReviewItemCard with inline actions | Approval must stay a human act (original design §PH-1) |
| Stock transfer between facilities | `transfer:write` | TransferProposalCard | Held by pharmacist and director; no tool today |
| Re-explore an NDC | `certification:explore` | Inline "Refresh from openFDA" on the dossier card | Spends external budget and mutates state (F-02, F-14) — should be deliberate |

Note the pattern: **every permission ending in `:write`, `:run` or `:approve` gets a card and a
button, and no tool performs it directly.** That is a rule worth stating in the tool-authoring
guide, because it keeps the "the model never commits" property structural rather than a matter of
each tool author's judgement.

---

## 4. Card catalogue by role

Each entry: which tools feed it, what it shows, what is interactive. Cards are composed from a small
set of shared primitives (§5), not designed one-off.

### 4.1 Pharmacist

**Shortage response brief** (PH-1) — feeds: `find_drug_by_name` (new, F-06), `check_stock_by_ndc`,
`search_analogues_rxnorm`, `verify_batch_cert`.

A single composite card, because the whole point of PH-1 is that these four answers belong together:

```
Propofol 10 mg/mL · RxCUI 8782 · NDC 16714097720
┌─ On hand ───────────────┬─ Cover ──────────┐
│ 250 units, 3 locations  │ ~8 days at burn  │
│ ▸ Main Room       180   │ ▁▂▃▅▇ depletion  │
│ ▸ Fridge B         50   │                  │
│ ▸ Bulk             20   │                  │
└─────────────────────────┴──────────────────┘
Substitutes ranked by on-hand here
 ● green  Ketamine 50 mg/mL      1 240 units   [Check for patient]
 ● yellow Etomidate 2 mg/mL        310 units   shortage
 ○ unknown Thiopental              0 units     no cert record
```

Interactive: expand a location row to batch/expiry; a substitute row opens its certificate card;
"Check for patient" hands off to the physician assessment card (physician role only).

**Certificate sweep** (PH-2, PR-1) — feeds `sweep_shelf_certificates`. Grouped red / yellow /
unknown with counts in the header, filter chips, per-row finding codes with a hover explanation,
sort by quantity or severity, and — closing an audit finding — an explicit **truncation banner**
when the tool sets `truncated: true`, which the model never mentions today. Bulk select → "Draft
orders for selected" (procurement only), which opens one proposal card per line.

**Cold-chain digest** (PH-3) — feeds `list_storage_excursions`. Facility → location tree, each
location a small temperature strip with the acceptable band shaded and excursions marked. Stock
sitting in a breaching location listed underneath with quantities. When `coverage.checked == 0`,
the entire card renders in the amber "not measured" state (§2.5) rather than showing an empty tree.

**NDC dossier** (PH-4, PR-2) — feeds `explore_ndc`, `verify_batch_cert`, `check_stock_by_ndc`.
Per-feed rows with an explicit tri-state, which prose consistently blurred in the audit:

```
openFDA NDC directory   ✓ consulted · matched
RxNorm status           ✓ consulted · active (2007-06 → 2026-08)
Recalls                 ✓ consulted · none
Import alerts           ✓ consulted · none
Warning letters         — not consulted
Shortages               — not consulted        ← F-02: this is why the verdict may be optimistic
```

Header shows the resolved NDC-11 and, when the input was malformed, an **"incomplete NDC" state**
with suggested package NDCs (F-07) instead of a confident verdict on a fabricated key.

### 4.2 Doctor

**Patient safety verdict** (DOC-1) — feeds `get_patient_regimen`, `assess_patient_for_drug`,
`check_stock_by_ndc`. The single highest-value card in the system:

```
┌──────────────────────────────────────────────────────┐
│ ⛔ DO NOT PRESCRIBE          Sulfamethoxazole/TMP    │
│                              Patient · age band 60-69│
├──────────────────────────────────────────────────────┤
│ HARD GATE   Documented allergy: sulfa                 │
│             ↳ rules engine, deterministic             │
│ CAUTION     Age ≥ 65 — renal dose review              │
│ OK          No interaction with recorded regimen      │
├──────────────────────────────────────────────────────┤
│ Availability   4 456 units · Main Pharmacy            │
│ Certification  ● yellow — discontinuing               │
├──────────────────────────────────────────────────────┤
│ [ Why was this flagged? ]  [ What can I give instead?]│
└──────────────────────────────────────────────────────┘
```

The verdict banner is coloured by the **rules engine's** output, never by the model's phrasing.
Findings are grouped by severity with the source of each named. Availability sits in the same card
because joining safety and availability is the entire premise of DOC-1.

**No name, no date of birth, ever** — the card shows the age band the tool returns (see §6).

The two buttons are pre-composed follow-up prompts, which is exactly how DOC-2 and DOC-3 were
specified: the value is in the conversation continuing, and a button removes the retyping.

**Analogue comparison** (DOC-2) — feeds `search_analogues_rxnorm` + per-candidate
`assess_patient_for_drug`. A ranked table rather than a list, because the physician is comparing:

| Substitute | Match | On hand here | Cert | For this patient |
|---|---|---|---|---|
| Ketamine 50 mg/mL | 92% | 1 240 | ● green | ✓ no findings |
| Etomidate 2 mg/mL | 78% | 310 | ● yellow | ⚠ age caution |

The last column is the one the audit showed missing: the model suggested alternatives without
re-checking them against the same allergy. Assessing each candidate is a tool call the model should
make, and the column's presence in the card contract is what makes its absence obvious.

**Verdict explanation** (DOC-3) — feeds `explain_assessment`. Per-factor contribution bars straight
from the payload, each labelled with the rule that produced it. **Every number rendered comes from
the card, so the model cannot introduce one** — the design rule §7.4 becomes structural instead of
aspirational. Footer: "Assessment `<request_id>` · ruleset `2026.08.2`".

**Patient snapshot** (DOC-4) — feeds `get_patient_regimen`. Allergy / condition / PGx chips, age
band, blood group. An explicit empty state for the medication list: *"This system does not track an
active medication list — check the chart."* The tool description says this; the audit could not
verify the model passes it on, and a card guarantees it.

**Patient picker** — already implemented for `patient_disambiguation`. Keep as is; it was one of the
few things that worked perfectly. Extend only to disable itself after a pick.

### 4.3 Director

**Morning digest** (DR-1) — feeds `list_at_risk_skus`, `list_storage_excursions`,
`sweep_shelf_certificates`. Three panels, each with its own coverage line and its own drill-in.
When per-facility grouping is unavailable (F-12), the card says so in the panel header — *"hospital-wide
totals; per-facility breakdown not available for certification"* — rather than silently presenting a
flat list as if it answered the question.

**Forecast run** (DR-2) — feeds `check_forecast_staleness`, then the new run tool. Two states in one
card: staleness ("last run 2026-08-18 21:31, data through 2026-08-17, 1 day stale") with a
**[ Re-run forecast ]** button, and after confirmation the delta table — SKUs entered / left /
worsened — computed in SQL and narrated, never computed by the model. This card *is* fix-plan F-11
Option B's user-facing half.

**Review queue** (DR-3) — feeds `list_review_queue`. Counts by status, accept rate, and the
`most_urgent` list **labelled by what it actually is**: *"ranked by reaction seriousness"*, not
"urgency" (F-15's sibling finding). Inline approve/reject for roles holding `profile:approve` —
the director does not, so those controls are absent for this role, not disabled.

**AI decision audit** (DR-4) — feeds `query_ai_decisions`. Outcome breakdown, tool-frequency bars,
p50/p95 latency, and a request-id lookup field. Two honesty requirements from the audit: the metric
is labelled **"AI assistant tool-call outcomes"** and never "error rate" unqualified (F-10), and the
lookup states its window — *"individually inspectable: last 10 turns"* — so an out-of-window id
reads as out-of-window rather than as never-happened (F-13).

### 4.4 Procurement

**Do-not-buy list** (PR-1) — the sweep card in purchasing mode: sorted by quantity on hand
descending, red rows non-selectable for ordering, and the 37 unknown-certification NDCs surfaced as
their own group with the label *"no certification record — not cleared for reorder"*. The model got
this right unprompted in the audit; the card makes it durable.

**Sourcing dossier** (PR-2) — the NDC dossier plus an **"already stocked?"** panel answering the
question PR-2 exists for and that no tool currently answers (fix-plan F-15): same ingredient, other
package NDCs, on-hand and supplier for each. Requires the new `find_drug_by_name` (F-06) and a
stock-by-RxCUI lookup.

**Cold-chain readiness** (PR-3) — the excursion data read as a receive/hold decision per site:

```
Central       ✓ can receive      12 readings · all in band
Riverside     ⛔ hold             fridge B +2.4 °C for 3 h
Westend       ⚠ unknown          no readings in 24 h    ← not a green light
```

The third row is the audit's dangerous case rendered so it cannot be mistaken for the first.

**Order proposal** (PR-5) — §3.2.

---

## 5. Shared primitives

Build these once; every card composes them. This is what keeps 15 cards from becoming 15 bespoke
components.

| Primitive | Used by | Notes |
|---|---|---|
| `<ComplianceChip status codes>` | every drug-bearing card | Four states — green / yellow / red / **unknown** — and unknown is visually distinct from green, never a neutral grey that reads as fine |
| `<CoverageLine coverage>` | every list card | §2.5 |
| `<DrugRef rxcui ndc name>` | everywhere | Click → certificate card; copy NDC; consistent identifier formatting |
| `<QuantityCell value unit>` | stock, orders | `tabular-nums`, thousands separators |
| `<SeverityList findings>` | assessment, certificate | Grouped, each with its rule source |
| `<ProposalFrame>` | all HITL cards | Confirm/Adjust/Cancel, disabled-with-reason, expiry countdown, idempotency key |
| `<ProvenanceFooter request_id tools>` | every card | §7 |
| `<EmptyState reason>` | every card | Distinguishes "none found" from "nothing checked" |
| `<TruncationBanner>` | sweep, at-risk | Fires on `truncated: true` |

---

## 6. PHI rules for cards

The audit verified that no database patient identifier reaches Gemini. Cards must not become the
leak — they travel over SSE, land in `CopilotMessage.card` in Postgres, and are re-served on reload.

1. **A card may carry only what its tool returned.** `get_patient_regimen` returns an age band, not
   a name or DOB — so the patient snapshot card carries an age band. The projector must not enrich
   from another query.
2. **The patient picker stays out of the card pipeline.** `patient_disambiguation` candidates carry
   real names and dates of birth. That event already bypasses the model (`copilot.py:252`); it must
   also bypass card persistence. Render it transiently, never write it to `CopilotMessage.card`.
3. **Cards inherit the caller's permissions and nothing more.** The projector receives `principal`
   for formatting decisions only — it must never widen a result.
4. **Card rows are covered by RLS** like every other `CopilotMessage`; `hospital_id` is set from the
   principal, server-side.
5. **A card is never sent back to the model.** Cards are a client-bound projection. The model's
   context keeps the raw `function_response` it already had; no card JSON is appended to
   `contents`. This keeps the model's view and the user's view independently auditable.

---

## 7. Provenance in the UI

Every card gets a footer that expands to what produced it:

```
Source · check_stock_by_ndc · 240 ms · request 995ca572…      [ ▾ ]
  args    { "ndc": "00338043304" }
  rows    5 locations
  digest  sha256:4f2a…
```

This is the client-visible half of fix-plan F-13. It also gives the reader a way to answer the
question the audit could not answer from outside the process: *did this number come from a tool?*
If a figure appears in the prose and in no card, that is now visible to a human at a glance.

---

## 8. Delivery phases

| Phase | Scope | Depends on |
|---|---|---|
| **P0 — wiring** | `cards.py` + projector registry, `tool_card` SSE event, persistence as `role="tool"` rows, TS type generation in CI, replay on conversation load | — |
| **P1 — read cards** | Certificate sweep, stock, analogues, patient snapshot, assessment verdict, excursions, at-risk, review queue, audit. Shared primitives (§5). Coverage states. | P0; fix-plan F-08 tool counts |
| **P2 — HITL** | `ProposalFrame`, `propose_order` replacing `draft_order`'s write, confirm endpoint with idempotency, expiry. Forecast-run proposal. | P1; **fix-plan F-03 and F-05 must be in first** |
| **P3 — provenance** | Card footers, request-id lookup, digests in `tools_called` | P1; fix-plan F-13 |
| **P4 — role polish** | Composite PH-1 brief, DOC-2 comparison table with per-candidate assessment, DR-1 three-panel digest, PR-3 readiness, bulk actions | P1–P3; fix-plan F-06, F-12 |

P0 is small and unlocks everything; it is worth doing even if P1 is deferred, because it converts
`tool_end` from a name into evidence.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Cards and prose disagree | The prose is the model's; the card is the data. Render the card **above** the prose and let the footer show the source. Add a lint probe: any figure in prose absent from every card in the turn is flagged in the audit log. |
| Projector exceptions break turns | `card_for()` swallows and logs (§2.3). No card is always an acceptable outcome. |
| The card union grows unbounded | One card kind per *workflow*, not per tool call. Composite cards (PH-1, DOC-1) are one kind with optional sections. |
| Type drift between Python and TS | Generation in CI, build fails on divergence (§2.2). This is exactly how the current `card` column drifted into being unused. |
| HITL becomes click-through fatigue | Only `:write` / `:run` / `:approve` get buttons. Read cards never interrupt. |
| Cards leak PHI into Postgres | §6, and one test asserting no card payload contains a `full_name` or `date_of_birth` key for any tool. |
