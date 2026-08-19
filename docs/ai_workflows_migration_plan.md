# AI Workflows — Migration Plan: Gemini SDK → LangChain + LangGraph + LangTrace

Moving the copilot from a hand-rolled Gemini tool loop to an orchestrated multi-agent graph, with
Pydantic contracts throughout and self-hosted OpenTelemetry tracing.

**Hard requirement: the system stays ZERO PHI.** No patient identifier may reach the model, the
checkpointer, the trace backend, or any log. §5 is the controlling section of this document; where
any other section conflicts with it, §5 wins.

Companion documents:

- [ai_workflows_fix_plan.md](ai_workflows_fix_plan.md) — defects to fix **before** migrating
- [ai_workflows_improve_plan.md](ai_workflows_improve_plan.md) — chat cards and HITL
- [ai_workflows.md](ai_workflows.md) — original design (stale)

---

## 0. Sequencing: fix first, then migrate

Do not migrate on top of the audit's defects. Two reasons, both practical:

1. **Migrating a broken tool preserves the break and hides its origin.** F-01 (all patient tools
   return "not found") would look like a porting error for a week.
2. **The audit's 73 probes are the migration's parity suite.** They are only a baseline if the
   system passes them first.

Minimum gate before Phase 1: **F-01, F-02, F-03, F-04, F-05** landed and verified.
Strongly recommended: F-06, F-07, F-08 — they change tool signatures, and changing signatures
during a port doubles the diff.

---

## 1. What we are moving, and what we are keeping

### 1.1 Today

`services/analogue/app/copilot.py` — one `async` route holding an SSE connection across a manual
tool-calling loop against `google.genai`. About 200 lines doing orchestration by hand:

```python
for _round in range(_MAX_TOOL_ROUNDS):        # 6
    if not b.allow(): ...                      # process-wide circuit breaker
    stream = await client().aio.models.generate_content_stream(...)
    async for chunk in stream: ...             # accumulate text + function_call parts
    if not function_call_parts: return         # done
    for part in function_call_parts:
        result = await execute(name, args, principal)   # permission re-check inside
        response_parts.append(Part.from_function_response(...))
```

Everything in that loop is a LangGraph concept implemented by hand: the round cap is a recursion
limit, the tool dispatch is a `ToolNode`, the SSE frames are stream events, the
`PatientAmbiguous` early return is an interrupt.

### 1.2 What survives the migration unchanged

These are the parts the audit found sound. **Porting must not weaken any of them.**

| Asset | Where | Why it survives |
|---|---|---|
| `PERMS` as the single source of truth | `shared/medstock_shared/auth.py` | RBAC held on all 30 adversarial probes |
| Two-layer permission enforcement | `registry.py:70` declare-filter + `:96` execute re-check | Declaration is a courtesy; the assert is the boundary |
| De-identification at the tool boundary | `pharmacy.py:441` — age band, never name/DOB | The zero-PHI guarantee lives here |
| Disambiguation bypassing the model | `copilot.py:252` | PHI candidates go to the client, never into model context |
| Audit rows carrying no prompt text | `copilot.py:118` | Nothing to redact because nothing is captured |
| Pydantic tool-argument models | `pharmacy.py` throughout | Already the LangChain idiom |
| Tools as pure `fn(args, principal) -> dict` | `registry.py` | Trivially wrappable; no rewrite |

### 1.3 What the migration buys

| Problem today | LangGraph answer |
|---|---|
| One monolithic prompt for four very different roles | Role-scoped specialist subgraphs with their own instructions |
| `_MAX_TOOL_ROUNDS = 6` as the only control flow | Explicit edges, conditional routing, bounded loops |
| No verification that narrated numbers came from tools | A verifier node between tool results and the answer |
| Nothing recoverable if a turn dies mid-way | Checkpointed state, resumable threads |
| HITL impossible — a tool writes or it doesn't | `interrupt()` is a first-class primitive |
| `tool_end` carries no payload; provenance unverifiable | Structured state + spans per node |
| Tuning = editing one prompt string and hoping | Per-node prompts, traced and independently evaluable |

---

## 2. Target architecture

### 2.1 Shape

A **supervisor** graph routing to four **specialist** subgraphs, each a bounded ReAct loop over the
subset of tools its domain needs. Specialists are domain-scoped, **not** role-scoped — the role
determines which tools bind, and the same specialist serves whichever roles hold those permissions.

```
                    ┌──────────────┐
   user turn  ──▶   │  input_guard │  policy: injection, PHI-in-prompt, scope
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │  supervisor  │  route by intent × available tools
                    └──┬───┬───┬───┴──┐
        ┌──────────────┘   │   │      └──────────────┐
        ▼                  ▼   ▼                     ▼
 ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────────┐
 │  clinical   │  │  inventory   │  │ compliance  │  │  oversight   │
 │   safety    │  │ & shortage   │  │ & sourcing  │  │ & forecast   │
 └──────┬──────┘  └──────┬───────┘  └──────┬──────┘  └──────┬───────┘
        └────────────────┴─────────┬───────┴────────────────┘
                                   ▼
                            ┌─────────────┐
                            │  tool_node  │  permission assert + execute
                            └──────┬──────┘
                                   ▼
                            ┌─────────────┐      ┌──────────────┐
                            │  verifier   │─────▶│ card_project │
                            └──────┬──────┘      └──────┬───────┘
                                   ▼                    ▼
                            ┌─────────────┐      ┌──────────────┐
                            │  responder  │      │  interrupt   │ HITL / disambiguation
                            └─────────────┘      └──────────────┘
```

| Node | Responsibility |
|---|---|
| `input_guard` | Deterministic pre-checks: injection heuristics, PHI-shaped input detection (§5.6), scope. No LLM. |
| `supervisor` | Picks a specialist, or answers directly for trivial turns. Sees tool *names*, never results. |
| `clinical_safety` | `assess_patient_for_drug`, `explain_assessment`, `get_patient_regimen`, analogues. **The only specialist touching patient-derived data.** |
| `inventory_shortage` | stock, analogues, at-risk, forecast reads |
| `compliance_sourcing` | certificates, sweep, explore, excursions, order proposals |
| `oversight` | review queue, AI audit, forecast staleness, cross-facility digest |
| `tool_node` | Single choke point for execution. Re-asserts permission per call (§3.2). |
| `verifier` | Asserts every figure in the draft answer traces to a tool result (§4.3) |
| `card_project` | Builds cards per the improve plan (§2.1 there); emits `tool_card` |
| `responder` | Final text; the only node whose output reaches the user as prose |

### 2.2 Why a supervisor rather than one agent

The audit found two failures that are routing failures, not reasoning failures:

- **F-09** — the model claimed it lacked permission for a tool it had just successfully called. One
  prompt carrying the full denied-tool inventory for all roles invites this.
- **F-10** — an infrastructure metric answered a clinical-quality question. A supervisor that
  routes "medication error rate" to `oversight` and finds no matching tool can return "not tracked"
  without an LLM improvising an adjacent answer.

Smaller, domain-scoped prompts with fewer bound tools are measurably less prone to both.

### 2.3 Why not more agents than this

Four specialists is the ceiling, not a starting point. Each additional agent adds a hop, a prompt to
maintain, and a place for the `Principal` to be dropped. The clinical/inventory/compliance/oversight
split maps onto the four permission clusters in `PERMS`; a finer split would not.

---

## 3. State, contracts and RBAC

### 3.1 Graph state — Pydantic, with a PHI-free invariant

```python
class Provenance(BaseModel):
    tool: str
    args_digest: str          # sha256 of canonical args — NOT the args
    row_count: int
    result_digest: str
    latency_ms: int


class CopilotState(BaseModel):
    """Everything checkpointed. Nothing here may be PHI -- see §5.3."""
    messages: Annotated[list[AnyMessage], add_messages]
    request_id: str
    role: str                              # from Principal; not the principal itself
    hospital_id: str                       # tenant scope, not an identifier of a person
    specialist: Literal["clinical", "inventory", "compliance", "oversight"] | None = None
    tool_calls_made: list[Provenance] = []
    cards: list[dict] = []                 # client-bound projections, PHI-free by §6 of improve plan
    pending_action: ProposalRef | None = None   # HITL
    verifier_retries: int = 0

    model_config = ConfigDict(extra="forbid")   # a stray field cannot smuggle PHI in
```

`extra="forbid"` is deliberate: state is checkpointed, and a permissive model is how an unreviewed
field ends up in durable storage.

### 3.2 The `Principal` never enters state

State is checkpointed and traced. The `Principal` is request-scoped authority — it belongs in
runtime context, not in a serialized graph state:

```python
@dataclass
class RuntimeCtx:
    principal: Principal

graph.invoke(state, config={"configurable": {"thread_id": tid}}, context=RuntimeCtx(principal=p))
```

Tools read it from context. The wrapper is where the boundary lives, and it is thin because the
existing registry already does the work:

```python
def as_langchain_tool(spec: ToolSpec) -> BaseTool:
    @tool(spec.name, args_schema=spec.args, description=spec.description)
    def _run(config: RunnableConfig, **kwargs) -> dict:
        principal = get_runtime(RuntimeCtx).context.principal
        # NOT a formality: binding is the courtesy layer, this is the boundary.
        if spec.permission not in PERMS.get(principal.role, set()):
            raise ToolDenied(f"{spec.name} requires {spec.permission}")
        return spec.fn(spec.args(**kwargs), principal)
    return _run
```

**Non-negotiable:** `llm.bind_tools(...)` is the analogue of `declarations_for()` — it decides what
the model is *told about*. It is not the security boundary. The assert above must exist inside every
tool, and a test must prove a denied tool raises even when it is bound by mistake. That is the
property `registry.py:96` gives us today and the audit confirmed; losing it in the port would be
the single worst possible regression.

### 3.3 Tool binding per role, computed once

```python
def tools_for(role: str) -> list[BaseTool]:
    granted = PERMS.get(role, set())
    return [as_langchain_tool(s) for s in REGISTRY.values() if s.permission in granted]
```

Built per specialist × role at graph construction, cached. The denied-tool listing that today goes
into the system prompt stays — it is why the copilot can say "you don't have permission" instead of
hallucinating — but it is now scoped to the specialist's domain, which addresses F-09's root cause.

---

## 4. Control flow

### 4.1 Bounded loops

| Loop | Bound | Replaces |
|---|---|---|
| Specialist ReAct (think → tool → observe) | `recursion_limit=12`, plus per-specialist `max_tool_calls=6` in state | `_MAX_TOOL_ROUNDS = 6` |
| Verifier retry (§4.3) | 2 | nothing — new |
| Supervisor re-route (specialist reports wrong domain) | 1 | nothing — new |

Every loop is bounded and every bound is in state, so exhaustion is a traceable event rather than a
silent truncation. Today, hitting the round cap emits a generic `degraded` frame that reads
identically to a Gemini outage; after migration these are distinguishable in traces and in the SSE
contract.

### 4.2 Interrupts — HITL and disambiguation

Both of the places today's loop returns early become `interrupt()`:

```python
# HITL: the order proposal from the improve plan §3
def propose_order_node(state: CopilotState) -> Command:
    proposal = build_proposal(...)          # read-only: prices, cert status, cited decision
    decision = interrupt({"kind": "order_proposal", "proposal": proposal.model_dump()})
    if decision.get("action") != "confirm":
        return Command(goto="responder", update={"cards": [cancelled_card(proposal)]})
    order = create_purchase_order(...)      # server re-validates F-03 and F-05 gates
    return Command(goto="responder", update={"cards": [order_card(order)]})
```

```python
# Disambiguation: PHI-bearing, and therefore special -- see §5.4
```

The graph resumes from the checkpoint when the user answers, which is a genuine improvement: today
an unanswered disambiguation loses the turn entirely.

### 4.3 The verifier node — making §7.4 structural

The original design rule — *"the model may narrate figures that appear in a tool result; it may not
compute one"* — is unenforceable today, and fix-plan F-13 explains why: tool payloads never leave
the process, so nothing can check. In the graph, the tool results are in state.

```python
def verifier(state: CopilotState) -> Command:
    draft = state.messages[-1].content
    unsourced = [n for n in extract_numbers(draft) if not traceable(n, state.tool_calls_made)]
    if unsourced and state.verifier_retries < 2:
        return Command(goto="responder", update={
            "verifier_retries": state.verifier_retries + 1,
            "messages": [SystemMessage(
                f"These figures appear in your answer but in no tool result: {unsourced}. "
                "Remove them or cite the tool that produced them.")],
        })
    if unsourced:
        record_metric("unsourced_figures", len(unsourced), request_id=state.request_id)
    return Command(goto="card_project")
```

Start it in **shadow mode** — measure, never rewrite — for two weeks. Numeric extraction has false
positives (dates, dosage strings quoted from a label, list indices), and a verifier that rewrites
correct answers is worse than none. Promote to enforcing only once the false-positive rate is known.

This node would have caught the fabricated NDC `0338-0519-01` in PH-1 and the invented RxCUI
`10041` in DOC-1 — both were identifiers present in no tool result.

### 4.4 Streaming: the SSE contract does not change

The frontend contract stays byte-compatible. `astream_events` is mapped to today's frames:

| LangGraph event | SSE frame |
|---|---|
| `on_chat_model_stream` (responder only) | `delta` |
| `on_tool_start` | `tool_start` |
| `on_tool_end` | `tool_end` + `tool_card` (improve plan §2.4) |
| `interrupt` — order proposal | `action_required` (new) |
| `interrupt` — patient ambiguity | `patient_disambiguation` (unchanged) |
| graph error / breaker | `degraded` |
| final | `done` with `request_id` |

Only the `responder` node's tokens stream as `delta`. Supervisor and specialist reasoning must
**not** — internal deliberation reaching the user is both confusing and a leak channel.

---

## 5. ZERO PHI — the controlling section

The current system achieves zero PHI through three specific mechanisms. LangGraph and LangTrace
each introduce a new durable store, and a naive port turns "PHI never reaches the model" into "PHI
now sits in two new places, one of them possibly off-box." This section is what prevents that.

### 5.1 The boundary as it exists today (verified in the audit)

1. `get_patient_regimen` (`pharmacy.py:441`) returns `age_band`, `blood_group`, `allergy_codes`,
   `condition_codes`, `pgx_phenotypes`. No `full_name`, no `date_of_birth`. De-identification
   happens **inside the tool**, before the result becomes a `function_response`.
2. `PatientAmbiguous.candidates` carries real names and dates of birth, but `copilot.py:252` catches
   it before any `function_response` is built, emits it as an SSE event to the frontend picker, and
   returns — the round loop never resumes, so the model is never re-invoked with it. Confirmed
   empirically: that turn produces zero `delta` events.
3. `AIAuditLog` never stores prompt text or patient identifiers (`copilot.py:118`) — there is
   nothing to redact because nothing is captured.

**All three must be preserved verbatim.** Rule 1 is a tool-level property and ports for free.
Rules 2 and 3 are architecture and are exactly what §5.3–§5.5 protect.

### 5.2 Trust zones after migration

| Zone | Contains | PHI allowed |
|---|---|---|
| Database (RLS-scoped) | `patient.full_name`, `date_of_birth` | **Yes** — the system of record |
| Tool internals | Row objects, pre-projection | **Yes**, transiently, in process |
| Tool return value | Projected dict | **No** |
| Graph state / `messages` | Model context | **No** |
| Checkpointer (Postgres) | Serialized state | **No** |
| LangTrace spans | Node IO, prompts | **No** |
| SSE to client | Frames | **Only** the disambiguation candidate channel (§5.4) |
| Gemini API | Prompt + tool results | **No** |

The line is drawn at the tool return value. Everything downstream is PHI-free by construction, and
every mechanism below enforces one crossing of that line.

### 5.3 Checkpointer

LangGraph checkpointers serialize the whole state, including `messages`, at every super-step. That
is a new durable copy of the model's context.

| Control | Requirement |
|---|---|
| Location | `PostgresSaver` against **our own** database, in the same tenant DB, never a managed/cloud checkpoint service |
| Schema | `CopilotState` with `extra="forbid"` (§3.1) — no unreviewed field can appear |
| Serializer | A wrapping serializer that runs the redactor (§5.6) over every payload before write. It should be a no-op; if it ever fires, that is an incident, and it emits a metric |
| RLS | Checkpoint tables carry `hospital_id` and are covered by the same row-level policies as every other table |
| Retention | TTL matching conversation retention; purging a conversation purges its checkpoints in the same transaction |
| Encryption | At rest, same as the patient tables |

**Test:** run the full audit probe suite, then scan every checkpoint blob for `full_name`,
`date_of_birth`, and any seeded patient name. Zero hits, or the migration does not ship.

### 5.4 The disambiguation interrupt is the one hard case

`interrupt()` payloads are **checkpointed** — that is how resumption works. Putting the PHI-bearing
candidate list in an interrupt payload would write names and dates of birth into the checkpointer,
destroying property 5.1.2.

The candidate list therefore never enters graph state:

```python
def resolve_patient_node(state, *, writer: StreamWriter) -> Command:
    try:
        pid = resolve_patient_ref(principal, state.pending_ref)
    except PatientAmbiguous as exc:
        # PHI leaves through the stream channel only -- never state, never interrupt payload.
        writer({"event": "patient_disambiguation", "candidates": exc.candidates})
        # The interrupt payload carries an opaque handle, nothing identifying.
        choice = interrupt({"kind": "patient_disambiguation", "token": exc.token})
        return Command(update={"pending_ref": choice["patient_id"]})   # a UUID, not a name
```

Two properties, both testable:

- The stream writer bypasses state; `exc.candidates` is never serialized.
- The interrupt payload holds an opaque token; the resume value is a UUID. A patient UUID is a
  tenant-scoped surrogate key, not an identifier of a person outside this system, and it is already
  what the tools accept.

**This is the single most likely place for the port to silently break zero-PHI.** It deserves its
own test file.

### 5.5 LangTrace

LangTrace is OpenTelemetry-based, which is what makes it acceptable here — spans go where we point
them.

| Control | Requirement |
|---|---|
| Deployment | **Self-hosted only.** No SaaS endpoint, no vendor egress. If self-hosting is not viable, the fallback is a plain OTel collector we operate — not a hosted alternative |
| Prompt/completion capture | **Disabled by default.** Capture span *metadata* — node name, duration, token counts, tool names, outcome — not content |
| Content capture in non-prod | Allowed **only** against synthetic data, on an environment with no production DB access |
| Redaction | The §5.6 redactor runs as a span processor before export, as defence in depth even with content capture off |
| Attribute policy | Allowlist, not denylist. An attribute not on the list is dropped. Denylists fail open, which is the wrong failure mode here |
| Retention | Bounded, and shorter than the conversation retention |
| Access | Same access controls as the audit log; trace access is a compliance-relevant capability |

What we get without content: per-node latency, tool-call frequency and failure rate, loop-exhaustion
counts, verifier firing rate, routing distribution. That is the useful part; capturing prompt bodies
in a medical system buys marginal debuggability for a large durable liability.

### 5.6 The redactor

One function, used in three places (checkpoint serializer, span processor, log filter):

```python
def redact(payload: Any) -> Any:
    """Structural PHI scrub. A no-op on correct data; an alarm on incorrect data."""
```

- **Structural first:** drop any key in `{full_name, date_of_birth, dob, mrn, ssn, address, phone,
  email, patient_name}` at any depth. Keys are reliable; regexes on free text are not.
- **Then heuristic:** date-shaped and MRN-shaped strings in free-text fields → `[REDACTED]`.
- **Always metric:** every redaction increments a counter tagged with the call site. In a correct
  system this counter stays at zero, which makes it an excellent alarm — a non-zero rate means the
  boundary in §5.2 has been crossed somewhere upstream.

### 5.7 User-typed PHI

A user can type a patient's name into chat. That text goes to the model verbatim today and will
after migration — inherent to a chat interface, and out of scope for a tool-boundary control.

What changes: `input_guard` (§2.1) detects PHI-shaped input and, rather than blocking, nudges —
*"you can refer to a patient by their ID; names are matched but not stored in this conversation."*
And, decisively, **user-typed PHI must not be checkpointed or traced**: the redactor runs over
`messages` on the checkpoint path, so a typed name is scrubbed before it becomes durable, even
though the model saw it in-flight.

### 5.8 Provider posture

Model calls stay with Gemini via `langchain-google-genai` — same provider, same key, same data
posture as today, so the migration does not change the vendor question. Whatever BAA or data-processing
terms cover the current usage continue to apply unchanged. Because the tool boundary is PHI-free,
what leaves the building is drug identifiers, stock counts, certification codes and de-identified
clinical attributes — the same payload as today.

---

## 6. Cross-cutting concerns to port

| Concern | Today | After |
|---|---|---|
| Circuit breaker | `shared_breaker()`, process-wide, 5 consecutive failures | Keep. Wrap the chat model in a `RunnableRetry` for 429s and keep the breaker for hard failures. Do not let LangChain retries mask an outage from the breaker |
| 429 handling | Explicitly not counted as a breaker failure (`copilot.py:210`) | Preserve exactly — it is a considered decision with a comment explaining itself |
| Audit rows | `write_audit` per turn | Keep, and enrich per fix-plan F-13: per-tool args digest and result digest. Written from a node, not the route |
| Idempotency | `ai_dedupe_key = request_id` | Maps to `thread_id` + checkpoint id. HITL confirms carry their own `proposal_id` key (improve plan §3.3) |
| Conversation persistence | `gateway.py` writes `CopilotMessage` rows | Unchanged. The checkpointer is graph-execution state; `CopilotMessage` remains the user-facing transcript. **Do not merge them** — they have different retention and different PHI rules |
| Degraded mode | `degraded` SSE frame; deterministic pages keep working | Unchanged, and strengthened: with the graph down, tools are still callable directly (original design §7.3) |

---

## 7. Phases

### Phase 0 — Prerequisites
Fix-plan F-01…F-05 landed. Audit's 73 probes captured as an automated parity suite with recorded
verdicts. Dependencies added to the `analogue` service only: `langgraph`, `langchain-core`,
`langchain-google-genai`, `langtrace-python-sdk`.

### Phase 1 — Tool layer
`as_langchain_tool()` wrapper (§3.2) over the existing registry. **No graph yet.** Existing loop
calls wrapped tools. Proves the permission assert survives wrapping, in isolation.
*Exit:* all 30 adversarial probes still pass; `tools_for(role)` matches `declarations_for()` exactly
for all four roles.

### Phase 2 — Single-agent graph
One ReAct graph replacing the manual loop. Same prompt, same tools, same SSE contract.
`InMemorySaver` — no durable checkpoint yet, so no new PHI surface.
*Exit:* parity suite at ≥ the recorded baseline; SSE frames byte-compatible; p95 latency within 20%.

### Phase 3 — Observability
LangTrace self-hosted, content capture off, allowlist attributes, redactor as span processor.
*Exit:* traces show per-node timing; redaction counter reads zero across the full suite.

### Phase 4 — Multi-agent
Supervisor + four specialists, per-specialist prompts and tool subsets. Verifier in **shadow mode**.
*Exit:* routing accuracy measured on the probe suite; F-09 and F-10 probes pass; unsourced-figure
rate published.

### Phase 5 — Durable state and HITL
`PostgresSaver` with the redacting serializer, RLS, TTL. `interrupt()` for order proposals and
disambiguation. Frontend `action_required` handling.
*Exit:* **the §5.3 checkpoint scan finds zero PHI**; §5.4 disambiguation test passes; a confirmed
order writes exactly one row and a double-confirm writes none extra.

### Phase 6 — Enforce and prune
Verifier to enforcing if its false-positive rate is acceptable. Delete the legacy loop. Rewrite
`ai_workflows.md` against the graph (fix-plan F-16).

Each phase is independently shippable and reversible. Nothing after Phase 2 requires a frontend
change except Phase 5's `action_required` frame.

---

## 8. Testing

**Parity suite** — the audit's 73 probes, replayed per phase with recorded verdicts. Any regression
blocks the phase. This is the reason §0 insists on fixing first: a suite recorded against broken
behaviour is not a baseline.

**Zero-PHI suite** — its own file, run in CI on every commit touching graph or trace code:

1. Full probe run → scan all checkpoint blobs for seeded patient names and DOBs. Zero hits.
2. Full probe run → scan all exported spans. Zero hits.
3. Disambiguation probe → assert `candidates` appears in the stream and in no checkpoint.
4. Assert `redact()` counter is zero across the suite.
5. Assert no tool return value contains a `full_name` or `date_of_birth` key, for every tool.

**RBAC suite** — for each role × each tool: bound tools match `PERMS`; a directly-invoked denied
tool raises `ToolDenied` **even when bound by mistake** (the property that must not be lost);
naming a denied tool in the prompt produces no tool call.

**Loop-bound suite** — a tool that always requests another call terminates at the recursion limit
with a distinguishable event, not a hang and not a generic `degraded`.

---

## 9. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Checkpointer becomes a PHI store | **Critical** | §5.3 — self-hosted, `extra="forbid"`, redacting serializer, RLS, CI scan |
| Disambiguation PHI enters an interrupt payload | **Critical** | §5.4 — stream channel + opaque token; dedicated test file |
| Tracing exports content off-box | **Critical** | §5.5 — self-hosted, content capture off, attribute allowlist |
| Permission assert lost in the tool wrapper | **Critical** | §3.2 — assert inside every tool; RBAC suite proves it with a deliberately mis-bound tool |
| Multi-agent widens effective access | High | Specialists share one `tool_node`; `PERMS` is still the only source of truth; no specialist holds credentials |
| Latency regression from added hops | Medium | Supervisor is a small fast call; specialists bind fewer tools. Budget: p95 within 20% at Phase 2, re-measured at Phase 4 |
| Verifier rewrites correct answers | Medium | Shadow mode first; promote on measured false-positive rate |
| Framework churn | Medium | Depend on `langchain-core` + `langgraph`; keep tool bodies framework-free `fn(args, principal) -> dict` so a future move is a wrapper change |
| Two loops in production during migration | Medium | Phases 1–2 keep one loop; feature-flag per hospital, not per request |
| Cost increase from more model calls | Low–Medium | Supervisor adds one small call per turn; specialists offset it with shorter prompts and fewer bound tools. Track cost per turn from Phase 3 |
