# Shared AI Engine & Reliability Layer — implementation plan

Status: **Phases 1, 2, 4 implemented** (breaker + versioned cache; `ai_audit_log` provenance;
copilot tool-calling in `analogue`). **Phase 3 (certificate OCR) skipped by decision** — it
targeted a UI flow `docs/backend/specs/H1-append-only-audit-log.md:11` calls "fabricated"; no
scanned-document concept exists anywhere in this schema. Phases 5–8 below are new proposals, not
yet started — see §9.

---

## 0. Corrections to the brief, before the plan

Five points in the request conflict with what is already in this repo. Resolving them first is
what keeps the plan buildable.

**0.1 There is no `app/`.** This is a `uv` workspace: `shared/medstock_shared/` (the library all
eight services import) and `services/<name>/app/`. A module at `app/services/ai/` would live
inside exactly one service and be unimportable by the other seven. **The module goes at
`shared/medstock_shared/ai/`** — promoting today's `ai.py` from a module to a package. No service
changes its import line: `from medstock_shared import ask_ai, AIError` keeps working.

**0.2 About 40% of the ask already exists and is in production use.**
[`shared/medstock_shared/ai.py`](../shared/medstock_shared/ai.py) already has the Gemini client
(lazy, so keyless services can import), SHA-256 `dedupe_key`, the `ai_cache` read/write with
DB-error-as-miss, tenacity retry on 429, and a per-task timeout override.
[`ai_tasks.py`](../shared/medstock_shared/ai_tasks.py) is the prompt/validator registry with two
live tasks (`analogue`, `prognosis`). Callers: `services/analogue/app/main.py:191` and
`services/ingest/app/prognosis.py:114`. This plan **extends** that file; it does not rewrite it.
Anything below that duplicates it is marked *already done*.

**0.3 `user_id` must not go on `ai_cache`.** `models.py:30-51` documents the omission as
deliberate: `ai_cache` holds reference-data answers (drug names, RxCUI, public label text),
never PHI, so two hospitals asking the identical question share one cached answer. Adding
`user_id` to the row either breaks that sharing or becomes a lie (the row is returned to users
who aren't the one named). **Provenance belongs on the audit row, not the cache row** — the
audit row records *who asked* and *which cache row answered them*. Every requirement in §2 of the
brief is still met; the `user_id` just moves one table over.

**0.4 There is no `audit_logs` table, and two audit designs are already half-specified.**
`assessment_log` (models.py:582) exists and is written by the rules engine; `audit_log_entry` is
specified in services.md §1.3 as trigger-written and `REVOKE UPDATE, DELETE`-protected, but is not
built. Creating a third scheme called `audit_logs` would make three. **Plan: build
`ai_audit_log` as a sibling of `assessment_log`** — same tenant/actor/request-id shape, same
append-only grant — rather than inventing a new convention. It is written from Python, not a
trigger, because the event being audited is an outbound API call, not a row mutation, so there is
no row for a trigger to hang off.

**0.5 Full-async is the wrong default here, and Redis isn't in the stack.**
- Every route in this repo is a plain `def`, and `db.py` uses sync SQLAlchemy. Making `ask_ai`
  `async def` without also converting the session layer puts a *blocking* `SessionLocal()` call
  inside the event loop — strictly worse than today's threadpool execution. **Plan: keep sync
  `ask_ai()` as the default path, add `ask_ai_async()` only for the copilot streaming endpoint**,
  which genuinely needs to hold a long-lived connection open without a thread. That one endpoint
  gets `AsyncSession`; nothing else changes.
- Redis is not deployed. A per-process in-memory breaker (~30 lines) is the right size at this
  system's volume (tens of calls/hour, 2 replicas): worst case two replicas learn about an outage
  independently, one extra failed call each. `ponytail:` note the ceiling; move to Redis when
  replica count or call volume makes that duplication cost real.

**0.6 `PydanticOutputParser` is LangChain.** The brief rules out LangChain in its first line, then
names one of its classes in §4.3. Gemini's SDK takes a Pydantic model directly as
`config.response_schema` and enforces it server-side — strictly better than parsing free text and
validating after. Plan uses `response_schema`.

---

## 1. Module file tree

```
shared/medstock_shared/
  ai/
    __init__.py          re-exports ask_ai, ask_ai_async, AIError, AIDegraded, dedupe_key
    core.py              ← today's ai.py, moved. Gemini call, retry, timeout, breaker hook
    breaker.py           NEW  CircuitBreaker: CLOSED/OPEN/HALF_OPEN, in-process
    cache.py             NEW  cache get/put (lifted out of core) + audit write
    tasks.py             ← today's ai_tasks.py, moved. + prompt_version on AITask
    schemas.py           NEW  Pydantic response models (certificate extraction, copilot turn)
    tools/
      __init__.py        NEW  registry + to_gemini_declarations(principal)
      registry.py        NEW  @tool decorator, ToolSpec, permission binding
      pharmacy.py        NEW  search_analogues_rxnorm, check_bioequivalence, verify_batch_cert
      procurement.py     NEW  get_stockout_forecast, generate_draft_po  (Phase 4)
      oversight.py       NEW  get_audit_trail, get_hospital_kpi_summary  (Phase 4)
  models.py              + AIAuditLog; ai_cache gains prompt_version, model_name
  auth.py                + PERMS entries for the new copilot permissions

services/analogue/app/
  copilot.py             NEW  POST /api/copilot/chat (SSE), the only async route

migrations/versions/
  20260818_ai_audit.py   NEW  ai_audit_log + ai_cache.prompt_version/model_name backfill
```

`ai.py` and `ai_tasks.py` become 2-line shims (`from .ai.core import *`) for one release, then
delete. `__init__.py`'s lazy `__getattr__` already isolates keyless services from the genai
import — extend its name set, keep the mechanism.

**Why `analogue` hosts the copilot** and not a new service: it already holds a Gemini key, RxNorm
access, and the closed-world filter the copilot's main tool wraps. A ninth Deployment for one
endpoint repeats the `ai-handler` mistake services.md §4 records. If the copilot's traffic ever
diverges from analogue's, split then.

---

## 2. Schema changes

```python
class AIAuditLog(Base):
    """Who asked the model what, and what came back. Tenant class, append-only.

    Sibling of assessment_log: same actor/request_id/hospital shape, because the
    audit read is the same read. Written from Python, not a trigger — the event is
    an outbound call, not a row mutation, so there is no row to hang a trigger on.
    """
    __tablename__ = "ai_audit_log"

    id:              Mapped[int]      # BigInteger PK
    hospital_id:     Mapped[str]
    actor_id:        Mapped[str]      # JWT sub
    request_id:      Mapped[str]      # echoed to the caller
    task_type:       Mapped[str]      # AITask.name
    dedupe_key:      Mapped[str]      # joins to the ai_cache row that answered
    prompt_version:  Mapped[str]
    model_name:      Mapped[str]
    outcome:         Mapped[str]      # cache_hit | live | fallback | breaker_open | error
    latency_ms:      Mapped[int]
    tools_called:    Mapped[dict]     # JSONB [] — copilot only
    created_at:      Mapped[datetime]

    __table_args__ = (
        Index("ix_ai_audit_hospital_time", "hospital_id", "created_at"),
        Index("ix_ai_audit_dedupe", "dedupe_key"),
    )
```

`ai_cache` gains `prompt_version: str` and `model_name: str`, and the unique constraint widens
to `(type, prompt_version, dedupe_key)`. **This is the provenance requirement's real teeth**:
today, editing a prompt in `ai_tasks.py` silently keeps serving answers generated by the old one.
Versioning the constraint means a prompt edit invalidates its own cache — which is exactly what
"same query + same prompt version = instant replay" requires, read in the contrapositive.

Migration backfills existing rows with `prompt_version='v1'` and the model that produced them.

`REVOKE UPDATE, DELETE ON ai_audit_log FROM app_role` ships in the same migration — same grant
services.md §1.3 specifies for `audit_log_entry`.

---

## 3. Interfaces

```python
# ai/tasks.py — AITask gains two fields, keeps the rest
@dataclass(frozen=True)
class AITask:
    name: str
    owner: str                                     # who gets paged when a prompt regresses
    prompt: str
    prompt_version: str = "v1"                     # NEW — bump on any prompt edit
    validate: Callable[[dict], None] | None = None
    response_schema: type[BaseModel] | None = None # NEW — native Gemini structured output
    timeout_seconds: float | None = None
    fallback: Callable[[dict], dict] | None = None # NEW — see §5


# ai/breaker.py
@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5      # consecutive 5xx/timeouts before OPEN
    recovery_seconds: float = 30.0  # OPEN → HALF_OPEN after this
    half_open_probes: int = 1       # successes in HALF_OPEN before CLOSED


class CircuitBreaker:
    state: Literal["CLOSED", "OPEN", "HALF_OPEN"]
    def allow(self) -> bool: ...
    def record(self, ok: bool) -> None: ...
```

Only 5xx and timeouts trip the breaker. **429 must not** — a rate limit is a signal to back off
this call, not evidence the provider is down; tripping on it would take the whole service off
Gemini because one caller was noisy. This matches `ai.py`'s existing `_Retryable` split, which
already treats 429 and 5xx as different animals.

```python
# ai/core.py
def ask_ai(
    task_name: str,
    payload: dict,
    *,
    principal: Principal | None = None,   # NEW — provenance; None = offline CronJob
    request_id: str | None = None,
) -> dict:
    """Unchanged contract: cached answer or a fresh one, AIError on failure.

    `principal` is what makes the call auditable. Omitted only by ingest's
    offline CronJobs, which are audited as actor_id='system:ingest'.
    """


async def ask_ai_async(task_name: str, payload: dict, *, principal: Principal) -> dict: ...


async def stream_copilot(
    messages: list[ChatTurn],
    principal: Principal,
    request_id: str,
) -> AsyncIterator[CopilotChunk]:
    """RBAC-filtered native function calling. Tool declarations are derived from
    principal.role via PERMS at call time — never cached, never passed in."""
```

```python
# ai/tools/registry.py
@dataclass(frozen=True)
class ToolSpec:
    name: str
    permission: str                                  # key into auth.PERMS
    description: str
    args: type[BaseModel]
    fn: Callable[[BaseModel, Principal], Awaitable[dict]]

def tool(permission: str) -> Callable: ...           # decorator, registers into _REGISTRY

def declarations_for(principal: Principal) -> list[dict]:
    """Gemini function declarations for exactly the tools this role may call."""
    granted = PERMS.get(principal.role, set())
    return [t.to_declaration() for t in _REGISTRY.values() if t.permission in granted]
```

**Tools bind to permissions, not roles.** The brief lists tools per role; binding them that way
would put the role→capability mapping in two places, and `auth.PERMS` is already the one place —
with a comment block explaining exactly why `profile:approve` is pharmacist-only. A tool naming a
permission inherits that reasoning for free, and a future role gets its tools automatically.

**Defence in depth**: `declarations_for()` filtering is not the security boundary — a model can
hallucinate a tool name it was never offered. Every `ToolSpec.fn` re-checks its own permission
against the passed `Principal` before doing anything, and every tool executes through
`session_scope()` with `app.hospital_id` set from that same principal, so RLS bounds it even if
both checks are wrong. Three layers, because the first is a model's cooperation and that is not a
guarantee.

---

## 4. Phasing

### Phase 1 — Reliability core (no new features)
`ai.py`/`ai_tasks.py` → `ai/` package. Add `breaker.py`, wire into `core.py`. Split `cache.py`
out. Add `prompt_version`/`model_name` to `ai_cache` + widened constraint. Migration.

*Acceptance:* `analogue` and `ingest` tests pass unchanged (import paths unmoved). Unit test
drives the breaker CLOSED→OPEN→HALF_OPEN→CLOSED against a stub that returns 503, and asserts
429 alone never opens it. Editing a prompt string + bumping `prompt_version` produces a cache
miss; not bumping it produces a hit.

### Phase 2 — Provenance
`AIAuditLog` model + migration + `REVOKE`. `ask_ai(principal=…)` threads through
`analogue/main.py` and `ingest/prognosis.py`. Fallback registration (`AITask.fallback`) and the
`AIDegraded` marker in returned payloads.

*Acceptance:* one `/analogue` request writes exactly one `ai_audit_log` row with the caller's
`sub`, and the `dedupe_key` on it selects the `ai_cache` row that answered. A repeat request
writes a second audit row with `outcome='cache_hit'` and makes no Gemini call. `UPDATE
ai_audit_log` fails as `app_role`.

### Phase 3 — Structured extraction (certificate OCR)
`schemas.py` with the certificate Pydantic model. New `extract_certificate` task using
`response_schema` + Gemini multimodal input. Wires into `services/compliance`, which already owns
`certification.py` and its 613 lines of deterministic rules.

*Acceptance:* a scanned certificate PDF yields a schema-valid object or a clean `AIError` — never
a partially-populated one. Fields the model could not read are `None`, not guessed. Extracted
values are shown alongside, never in place of, the deterministic `drug_certification` record.

### Phase 4 — Copilot
`tools/` registry, `pharmacy.py` tools (the three that have backing code today), the SSE
endpoint, new `copilot:chat` permission in `PERMS`. `procurement.py`/`oversight.py` tools are
scaffolded but **registered only as their endpoints land** — `generate_draft_po`,
`approve_and_send_po`, and `approve_emergency_protocol` have no implementation anywhere in this
repo, and six of the eight services are still `/healthz` skeletons (services.md §4). A tool
declared to Gemini with no endpoint behind it is a hallucination generator.

*Acceptance:* a physician JWT receives a tool list with no `verify_batch_cert` in it; a forged
call to it from the model returns a tool-error to the model, not a result, and writes an audit
row. `tools_called` on the audit row lists every invocation of the turn. Stream survives a
mid-turn breaker trip by emitting a degraded final chunk, not a broken SSE stream.

**Never auto-approve.** services.md §4 already states it for recommendations; it binds harder
here. `approve_and_send_po` and `approve_emergency_protocol` are write actions with outside-world
consequences, and a copilot that can call them is a copilot that can be prompt-injected into
calling them via drug-label text it was asked to summarise. Plan: those two are **not tools**.
The copilot can *draft* and hand the draft to the existing human approval UI; a person clicks
send. This is the one requirement in the brief I'd push back on outright.

---

## 5. Failure modes & fallback matrix

| Condition | Breaker | `ask_ai()` behaviour | Caller-visible result | Audit `outcome` |
|---|---|---|---|---|
| Cache hit | untouched | returns cached row, no network | normal, instant | `cache_hit` |
| Gemini 200 | `record(ok)` | validate → cache → return | normal | `live` |
| Gemini 429 | **untouched** | tenacity backoff, ≤3 attempts | normal if a retry lands, else `AIError` | `live` / `error` |
| Gemini 5xx | `record(fail)` | no retry (existing policy) | task fallback if registered, else `AIError` | `fallback` / `error` |
| Timeout (>4 s request path) | `record(fail)` | as 5xx | as 5xx | `fallback` / `error` |
| Breaker OPEN | — | **no network call at all** | fallback immediately, ~0 ms | `breaker_open` |
| Breaker HALF_OPEN | one probe allowed | probe runs; others get fallback | mixed | `live` / `breaker_open` |
| Schema/validate failure | `record(ok)` | `validate()` prunes or raises | pruned result, or `AIError` | `live` / `error` |
| `ai_cache` DB error | untouched | treated as miss (already done) | normal, uncached | `live` |
| No `GEMINI_API_KEY` | n/a | `_ai_available()` false, never called | deterministic path only | not written |

**Per-task fallbacks** (`AITask.fallback`), all deterministic, all already implemented in some
form:

| Task | Fallback | Where it exists today |
|---|---|---|
| `analogue` | unfiltered RxNorm candidate list + `ai_degraded: true` | `main.py:_filter_full_with_ai` returns `(items, True)` |
| `prognosis` | skip the drug this cycle; CronJob retries next run | `ingest/prognosis.py` retry loop |
| `extract_certificate` | no extraction; deterministic `drug_certification` only | `compliance/certification.py` |
| `copilot` | non-streaming message: "AI assistant unavailable, deterministic search still works", plus a direct link to the analogue/inventory UI | new |

**The invariant across all of them:** AI degradation removes a *ranking or a convenience*, never a
*safety check*. Nothing in the certification rules, the RxNorm graph, the stock query, or the
assessment engine depends on Gemini answering. If Gemini is down for a day, MedStock is a less
pleasant deterministic tool — not an unsafe one. Any future task that cannot state a fallback in
one line of this table does not belong on the request path.

---

## 6. What this plan deliberately does not build

| Skipped | Add when |
|---|---|
| Redis-backed breaker | replica count > 4, or duplicated outage-learning shows up in the audit log |
| Full async DB layer | more than one endpoint needs it; converting `db.py` for the copilot alone is a 7-service blast radius (services.md §6) |
| Queue / `ai-handler` revival | concurrent Gemini calls need a system-wide ceiling — the exact trigger services.md §4 already names |
| `user_id` on `ai_cache` | never; it belongs on `ai_audit_log` (§0.3) |
| Procurement/oversight tools | their endpoints exist |
| A separate `copilot` service | copilot traffic diverges from analogue's |

---

# Part II — Phase 5–8: new use cases

Proposals, not yet approved for implementation. Each was checked against what actually exists —
table, endpoint, permission — before being written down; two of them are gaps `docs/services.md`
§4 and `docs/pre-mortem.md` already named as open (`extract`, `prediction`). One throughline
holds across all four: everywhere this system uses AI today, the model **ranks, narrates, or
extracts from closed-world data with a citation — it never invents a number or a verdict**. Every
phase below keeps that invariant; where a natural design would break it, that's called out and
designed around, not silently done.

## 9. Phase 5 — `explain_assessment` narration (pharmacist)

**Where:** `services/patient-profiling/app/main.py`'s `explain` endpoint
([main.py:634](../services/patient-profiling/app/main.py)). Deliberately deterministic today —
its own docstring: "the contributions are not estimated, they are the arithmetic itself," which
is what lets it satisfy the FDA CDS exclusion's criterion (d), a professional must be able to
independently review the *basis* of a recommendation. That basis must stay the arithmetic. The
model's job here is narrower than anywhere else in this codebase: **paraphrase already-computed,
already-cited numbers into a sentence** — not decide anything, not add a fact the response body
doesn't already contain.

**Task:**

```python
TASKS["explain_assessment"] = AITask(
    name="explain_assessment",
    owner="Andrii",
    prompt_version="v1",
    prompt=(
        "A hospital's automated drug-safety rules produced this assessment. Write one or two "
        "plain-language sentences a pharmacist can read at a glance. State only what is in the "
        "data below — do not add a risk, a number, or a recommendation that isn't already "
        "there.\n\nVerdict: {verdict}\nScore: {score}\nContributions (code, weight, share of "
        "total): {contributions}\n\nReturn JSON: {{\"narrative\": str, \"cites\": [str]}} — "
        "each entry in cites must be a code from Contributions above, verbatim."
    ),
    validate=_no_uncited_numbers,  # NEW — see below
)
```

**A new validator, not a variant of the citation-substring check.** The existing
`_citation_must_be_verbatim` checks that a quoted *sentence* appears in source text; this needs
to check that no quoted *number* appears that isn't in the source data. `_no_uncited_numbers`:
extract every digit sequence from `result["narrative"]`, reject the whole narrative (not prune —
there is no partial-sentence salvage here) if any number isn't one of the source's `score`,
`weight`, or `share` values. A pharmacist reading a wrong number in a sentence next to a correct
one is worse than reading no sentence.

**Caching changes the trust story favorably, not just the cost story.** `AssessmentLog` rows are
immutable once written (`docs/patient-profiling-usecases.md` §7), so the same `request_id` always
narrates the same way — this is the most cacheable task in the system, not the least. Dedupe key
on `{verdict, score, contributions}` (never `request_id` or `patient_ref` — no PHI enters the
payload, matching `AssessmentLog`'s own no-identifier design), so two different patients who land
on an identical scored outcome share one cached sentence, same sharing model as `ai_cache`
already has for `analogue`.

**Architectural correction this phase forces:** `docs/services.md` §3 states "only `analogue`
and `prediction` call Gemini on a request path." Serving a narration inline in `explain()` makes
`patient-profiling` a third. Two ways to keep the rule intact instead, in order of preference:

1. **Precompute, don't call live.** A `CronJob` narrates every `AssessmentLog` row shortly after
   it's written (matching `prognosis`'s already-established "offline, nobody is waiting" pattern)
   and stores the result back onto the row or in `ai_cache`. `explain()` stays a plain read —
   zero new request-path Gemini caller, zero new latency, zero new failure mode on a
   compliance-critical endpoint.
2. **Call live, but only as an additive field the response degrades without.** `explain()` calls
   `ask_ai` itself, patient-profiling gets a Gemini secret, and `narrative: str | None` is added
   to the response — `null` on any `AIError`, same fallback discipline as `analogue`. This does
   revise services.md §3's "only two" claim and should be written up as an amendment, not slipped
   in silently.

Recommend (1): it keeps the pharmacist-facing compliance endpoint exactly as fast and dependency-
free as its own docstring says it needs to be, and the caching argument above means there's no
"same request twice" case (2) would even help with, since a request is only ever asked about the
same immutable assessment once.

**Acceptance:** `explain()`'s response gains an optional `narrative` field; every existing field
is unchanged. A narrative containing a number absent from `contributions` is rejected before
storage — a test asserts this directly by constructing a result with a fabricated number and
checking `_no_uncited_numbers` raises. Two assessments with identical `(verdict, score,
contributions)` for different patients produce one Gemini call, not two.

## 10. Phase 6 — `extract_recall_identity` (COMP-2, ingest)

**Where:** `services/ingest/app/certification.py`, which already names this exact gap at
[line 20](../services/ingest/app/certification.py): 61% of ongoing FDA recalls carry no
`openfda.package_ndc` and are dropped rather than joined, "extracting identity from that free
text is COMP-2's `extract` task, and this is the concrete reason it exists." Fully offline — a
`CronJob`, no request ever waits on it, no patient data anywhere near the call (public recall
text), the same trade `prognosis` already made.

**Task, using `response_schema` for real for the first time** (`AITask.response_schema`, drafted
in Phase 1's interfaces but never given a consumer since Phase 3 was skipped):

```python
class RecallIdentity(BaseModel):
    ndc: str | None = Field(description="An 11-digit NDC named in the text, or null")
    product_name: str | None
    confidence: Literal["high", "medium", "low"]
    citation: str  # verbatim substring of product_description

TASKS["extract_recall_identity"] = AITask(
    name="extract_recall_identity",
    owner="Andrii",
    prompt_version="v1",
    prompt=(
        "An FDA recall names a product only in free text, with no structured NDC. Read the "
        "text and identify the NDC if one is stated. Do not guess a resemblance — if no NDC "
        "appears verbatim in the text, return null.\n\nText: {product_description}"
    ),
    response_schema=RecallIdentity,
    validate=_recall_identity_is_grounded,  # NEW
)
```

**Two-layer grounding, not one.** `_recall_identity_is_grounded` rejects the result if `citation`
isn't a verbatim substring of `product_description` (the established pattern) — but a
well-formatted, verbatim-quoted NDC can still be one that doesn't exist. So `certification.py`'s
caller does a second check the validator can't: the extracted `ndc`, if any, must appear in the
NDC Directory index (`_product_ndcs`/`_package_ndcs`) this same job already builds from the other
feed — closed-world, exactly like `analogue`'s `by_rxcui` filter rejects any RxCUI outside the
actual candidate set. An extraction that names a real-sounding NDC nobody's directory has ever
heard of is discarded, not trusted.

**A finding built on inference should not carry the same weight as one built on a structured
field, and this codebase already has the pattern for that distinction.** `certification.py`'s own
rules cap news/unverified findings at yellow — "News and unverified reports can never produce
red" (`docs/compliance-usecases.md` §4.3). Apply the identical cap here: a `CertificationFinding`
whose NDC came from `extract_recall_identity` is tagged (a new `source='ai_extracted'` value, or
reuse the existing `source` column) and the rule that would otherwise map a Class I recall to red
is capped at yellow when the join is inferred rather than structured. No new approval workflow to
build — the severity ceiling *is* the review gate, same as it already is for news.

**Acceptance:** a synthetic `product_description` naming a real NDC from the demo seed data
produces a `CertificationFinding` capped at yellow even where the recall's own classification is
Class I. A `product_description` naming no real NDC, or one the directory doesn't have, produces
no finding at all — never a hallucinated one. Re-running the CronJob twice on the same recall
text is one Gemini call, not two (cache hit on the second).

## 11. Phase 7 — deterministic forecast + narration (`prediction`, director/procurement)

**Where:** `services/prediction`, today a `/healthz` skeleton with one test — the gap
`docs/pre-mortem.md` §2 names directly ("`prediction` is a `/healthz` stub"). What's changed
since that doc was written: `warehouse.ConsumptionDaily`
([models.py:764](../shared/medstock_shared/models.py)) now exists, with real per-facility daily
usage and a `stockout` flag marking recorded zeros that are censoring (empty shelf), not absence
of demand — the data half of this gap is closed, only the endpoint isn't built.

**Correction to `docs/services.md` §4's own task table**, before building this: it describes the
`prediction` task as the model producing "days + confidence + reasoning" directly. That would be
the one place in this design where an LLM invents a quantity on a supply-planning path, breaking
the invariant every other task holds to. Split it instead:

1. **Deterministic forecast — no model involved.** Days-of-supply = current `on_hand` ÷ a moving
   average of `qty_consumed` over the trailing N days, **excluding** `stockout=true` days from
   the average (a censored zero pulls the average down and the forecast falsely optimistic if
   left in). This is a genuinely new algorithm to write — plain arithmetic, not a task for
   `ask_ai` at all.
2. **`ask_ai("narrate_forecast", …)` — reasoning only, grounded in (1)'s own numbers.** Same
   `_no_uncited_numbers`-style validator as Phase 5: the narrative may cite `days_of_supply`,
   the trend direction, and the drug's current shortage/certification status (already-computed,
   already-available), and nothing else. "Consumption rose 22% over the trailing 14 days; at the
   current rate this drug has 9 days of supply left" is fine — the 22% and the 9 both have to
   already be in the payload the model was given, or the sentence is rejected.

**Acceptance:** `GET /forecast/{rxcui}` returns `{days_of_supply, trend, confidence, narrative}`
where the first three are computed with zero Gemini calls and are testable with plain arithmetic
fixtures; `narrative` is `None` on any `AIError` (same degrade discipline as everywhere else) and
the endpoint's numeric fields are unaffected by that failure. A test constructs a narrative citing
a number absent from the forecast payload and confirms it's rejected before being returned.

## 12. Phase 8 — `mapping_spec` (connector-factory, admin)

**Where:** new — `docs/services.md:23` names it directly ("the `mapping_spec` AI task
[connector-factory] would use goes through `ask_ai()` like any other task, not a separate
service") and `admin`'s `mapping:approve` permission has existed in `PERMS` with zero backing
code since before this AI module existed. Largest scope of the four; sequenced last for that
reason, and **blocked on `docs/backend/specs/B6-formulary-import.md` (❌, not built)** — B6 is
the deterministic CSV import this task is an *assist* on top of, not a replacement for. A file
whose columns already read `rxcui,name` needs no AI; `mapping_spec`'s whole job is proposing the
translation when they don't.

**The model never executes anything — this is the safety property the name is chosen to make
inescapable.** It proposes a **declarative** mapping — `{source_column: canonical_field,
transform}` where `transform` is one of a fixed enum (`trim`, `uppercase`, `date_parse:<fmt>`,
`identity`) the ingest job already knows how to interpret — never a code string, never `eval`.
The admin reviews the *spec*, not a diff of imported rows, and only an approved spec is ever run:

```python
class ColumnMapping(BaseModel):
    source_column: str
    canonical_field: Literal["rxcui", "name"]  # grows only as B6 grows more columns
    transform: Literal["trim", "uppercase", "identity"] | str  # "date_parse:%Y-%m-%d" pattern-matched, not free text
    confidence: Literal["high", "medium", "low"]

class MappingSpec(BaseModel):
    columns: list[ColumnMapping]
    unmapped_columns: list[str]  # named, not silently dropped — an admin should see what didn't map

TASKS["mapping_spec"] = AITask(
    name="mapping_spec", owner="Mykhailo", prompt_version="v1",
    prompt="Given these CSV column headers and 3 sample rows, propose how each maps onto our "
           "canonical fields ({canonical_fields}). Only use the listed transforms. If a column "
           "doesn't map, list it in unmapped_columns rather than guessing.\n\n"
           "Headers: {headers}\nSample rows: {sample_rows}",
    response_schema=MappingSpec,
)
```

**Schema (new table, tenant-owned per `services.md`'s own classification table)**:

```sql
CREATE TABLE mapping_spec (
  id bigserial PRIMARY KEY,
  hospital_id text NOT NULL,
  source_name text NOT NULL,        -- filename or connector label, not sensitive
  spec jsonb NOT NULL,               -- the MappingSpec, as proposed or as edited
  status text NOT NULL DEFAULT 'awaiting_approval',  -- awaiting_approval | approved | rejected
  proposed_by text NOT NULL,         -- 'ai' or an actor_id, if hand-edited before approval
  approved_by text,
  created_at timestamptz NOT NULL DEFAULT now(),
  approved_at timestamptz
);
```

**Flow:** admin uploads headers + a sample (never the full file — the model sees structure, not
data volume) → `ask_ai("mapping_spec", …)` → stored `awaiting_approval` → admin reviews in the UI,
can hand-edit any `ColumnMapping` before approving → `mapping:approve` flips `status` → **only
then** does a full B6-style import run, through a small declarative interpreter (a `match` over
the four fixed transforms) that executes the *approved* spec — never model output directly, and
never code the model wrote.

**Acceptance:** an admin uploads headers `Drug Code, Drug Name, First Seen` against canonical
`rxcui, name` and receives a spec mapping the first two and naming `First Seen` in
`unmapped_columns` — never a mapping to a field that doesn't exist in `canonical_fields`.
Approving a spec then importing runs the fixed interpreter, not the free-text `transform` value —
a spec with a `transform` outside the enum fails to parse into `ColumnMapping` at all (Pydantic
rejects it) and is never stored as approvable. Two identical header sets share one cached
proposal.

## 13. Sequencing and what's still open

| Phase | Depends on | Size | Why this order |
|---|---|---|---|
| 5 — `explain_assessment` | Phases 1–2 (done) | Small | Safest: reuses the proven pattern exactly, zero new infra |
| 6 — `extract_recall_identity` | Phases 1–2 (done) | Small–medium | Already-named gap, fully offline, no request-path risk |
| 7 — `prediction` forecast + narration | Phases 1–2 (done), a new deterministic algorithm | Medium | Data now exists (`ConsumptionDaily`); closes a pre-mortem-flagged gap |
| 8 — `mapping_spec` | **B6 (not built)** | Large | New table, new endpoints, new UI review flow, gated on a prerequisite that doesn't exist yet |

None of these are approved for implementation — say which one (or several) to build and I'll plan
it out to code-signature depth the way Phases 1–4 were, then implement it the same way.
