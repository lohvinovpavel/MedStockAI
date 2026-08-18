# Shared AI Engine & Reliability Layer — implementation plan

Status: proposal. Supersedes nothing yet; amends [services.md](services.md) §4 when Phase 1 lands.

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
