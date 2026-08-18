# MedStockAI — Service Specification

Companion to [architecture.html](architecture.html). Per-process responsibilities, data flow, and lifecycle.

Status: **draft** — decisions marked `OPEN` are not settled.

---

## 0. Preface: two logical blocks, eight processes

The team settled on two blocks — **WEB** and **API** — where API is seven independently
deployed services, one owner pair per developer.

This supersedes the earlier four-process model (`web` / `api` / `worker` / `ingest`). What
happened to those boxes:

| Earlier model | Now |
|---|---|
| `api` | Split into seven domain services, one per bounded area |
| `worker` | **Removed entirely.** Its job was async LLM processing behind a queue; §4 covers why that queue is gone and Gemini is now called directly, synchronously, from the two services that need it |
| `ingest` | Removed as a service, coming back as an eighth image running three `CronJob`s — see §7/§8 |
| `exposure-engine` | A SQL query, executed inside `inventory` |
| `connector-factory` | Admin endpoints in `warehouse`; the `mapping_spec` AI task it would use goes through `ask_ai()` like any other task, not a separate service |

What gets deployed:

```
image: medstock-web                     →  web         (Deployment, 2 replicas)
image: medstock-<service>  ×7           →  auth        (Deployment)
  built from one Dockerfile,               inventory   (Deployment)
  --build-arg SERVICE=<name>               analogue    (Deployment)
                                           compliance  (Deployment)
                                           patient-profiling (Deployment)
                                           prediction  (Deployment)
                                           warehouse   (Deployment)
image: medstock-ingest                  →  ingest-shortages (CronJob, hourly)
  same Dockerfile, --build-arg               ingest-pricing   (CronJob, daily)
  SERVICE=ingest, command overridden          ingest-rxnorm    (CronJob, weekly)
  per CronJob (no HTTP app to serve)
```

**One Dockerfile, eight images.** Seven differ only by `command` target module and env; the
eighth (`ingest`) differs more — no FastAPI app, no `/healthz`, the Dockerfile's default
`CMD ["uvicorn", …]` is never used because every `ingest-*` CronJob sets its own `command`
(`python -m app.shortages`, etc — see `services/ingest/README.md`). CI builds them as a
matrix; a service that grows a real system dependency gets its own Dockerfile at that point,
not before.

### Say this out loud at defense

**The deploy boundary is real; the data boundary is not.** All seven services share one
Postgres database and one `shared/` package. That makes this a *distributed monolith*, and it
was chosen deliberately:

- Seven deploy units give four developers parallel work, independent rollout, and isolated
  process failure.
- One schema under row-level security gives correctness that seven-way HTTP choreography
  would not, on a capstone timeline. `prediction` needs to join stock against shortages; over
  HTTP that becomes pagination, retries, and fixtures for fake upstreams instead of the drug
  logic that is the actual product.

The cost, stated plainly: **a change to `shared/` redeploys all seven.** That is the price of
the trade, and it is cheaper than the alternative.

The service boundaries follow ownership, not load profile. `compliance` and `analogue` will
never need to scale apart. That is an honest answer — do not invent a technical justification
for it.

---

## 1. Shared foundations

Everything below assumes these mechanisms. They are described once because all seven services
depend on them identically.

### 1.1 Two classes of table, plus one that's neither

| Class | Tables | RLS | Written by |
|---|---|---|---|
| **Reference** (global, shared by all hospitals) | `drug`, `shortage_event`, `drug_price`, `rxnorm_edge` | no | `ingest`'s three `CronJob`s — code exists (`services/ingest`), not yet scheduled or migrated, see §8 |
| **Tenant** (owned by one hospital) | `formulary_item`, `stock_snapshot`, `recommendation`, `review_decision`, `audit_log_entry`, `mapping_spec` | **yes** | the owning service |

The split is load-bearing. Reference data is polled once for all hospitals — this is why the
cost curve is sub-linear in site count. Tenant data never leaves its hospital, enforced by
row-level security, not by application code.

There is a third table, `ai_cache` (§4), that fits neither row on purpose: it's global like
reference data (no `hospital_id`, no RLS — Gemini answers for reference-data questions are
shared across every hospital asking the same one), but it isn't reference data itself, it's a
memoized function result. Don't file it under either class above; it's its own thing.

### 1.2 Tenant context is set, never filtered

Every service opens a transaction and declares who it is acting as before touching tenant
tables:

```sql
SET LOCAL app.hospital_id = '…';
SET LOCAL app.actor_id    = '…';
```

In code this is `medstock_shared.session_scope(hospital_id, actor_id)` — a context manager,
so there is no path that opens a tenant transaction without setting it.

All seven request-serving services read both from the verified JWT — there is no background
process reading them from anywhere else. `ask_ai()`'s Gemini calls and its `ai_cache` writes
(§4) don't go through `session_scope` at all, because `ai_cache` isn't a tenant table (§1.1);
there is no hospital context to set for it.

`SET LOCAL` is transaction-scoped, so nothing leaks across pooled connections. The
application connects as a role **without** `BYPASSRLS` and which does not own the tables —
a role that owns a table ignores its own policies.

### 1.3 The audit log writes itself

The audit trail is the product's compliance value; "we remember to call `audit()`" is the same
weak guarantee as "we remember to write `WHERE hospital_id`". With seven services it is a
worse guarantee — it has to hold in seven codebases. So it is a trigger:

```sql
CREATE TRIGGER audit_review_decision
  AFTER INSERT OR UPDATE ON review_decision
  FOR EACH ROW EXECUTE FUNCTION write_audit_entry();
-- write_audit_entry() reads current_setting('app.actor_id') and current_setting('app.hospital_id')

REVOKE UPDATE, DELETE ON audit_log_entry FROM app_role;
```

Append-only is a **grant**, not a convention. This is demonstrable in ten seconds at defense.

### 1.4 Authentication is verified locally, not asked for

`auth` issues tokens. It is **not** called on the request path.

Every other service verifies the JWT signature locally with a public key it already holds, and
reads `hospital_id` and `role` from the claims. A service that had to call `auth` on every
request would make `auth` a single point of failure for the other six — one pod restart and
the whole system returns 401.

The verification code and the permission map live in `shared/medstock_shared/auth.py`. RBAC is
one dependency per route: `Depends(require("queue:read"))`.

Object-level authorization is a side effect of §1.2, not separate code: a row belonging to
another hospital is simply not visible, and the handler returns `404`. There is no
"is this mine?" check to forget.

### 1.5 There is no queue — `ai_cache` is a cache, not a job table

```sql
CREATE TABLE ai_cache (
  id           bigserial PRIMARY KEY,
  type         text NOT NULL,        -- analogue | prediction | extract | mapping_spec
  dedupe_key   text NOT NULL,        -- sha256(type + canonical_json(payload))
  result       jsonb NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (type, dedupe_key)
);
```

No Redis, no Pub/Sub, and — since the `worker`/`ai-handler` removal (§4) — no `status`,
`attempts`, or `locked_at` either. There is nothing to dequeue: `ask_ai()` calls Gemini inline,
in the request that needed the answer, and writes the result here only so the next identical
question is free.

**Lives in `shared/`, called from `analogue` and `prediction`.** No hospital scoping — see the
note under §1.1 for why that's deliberate, not an oversight.

`UNIQUE (type, dedupe_key)` is what makes the table a cache — the same question is never paid
for twice. See §4.

---

## 2. `web` — user interface

**Runs:** Next.js, `Deployment`, 2 replicas, no autoscaling.

The two boxes on the whiteboard were replicas, not two applications. There is one web app.

### Responsibility

Renders three role-scoped surfaces over one dataset:

| Persona | Surface |
|---|---|
| Pharmacist | Review queue — ranked alternatives, citations, approve / edit / reject |
| Physician | Point-of-order prompt: "this drug is in shortage; here is the approved alternative" |
| Director | Formulary-wide risk + savings dashboard, compliance export |

### Talks to

- **Browser** (inbound, via Ingress).
- **Nothing else.** `web` serves the application shell and static assets. The browser calls
  the seven public services **directly** on the same origin (`/api/*` routed by Ingress).

That last point is a deliberate choice: routing API traffic *through* the Next.js server
would add a hop, force token forwarding through Node, and make `web` an availability
dependency of every data request. Same-origin means the session cookie is `httpOnly`,
`Secure`, `SameSite=Lax` and no CORS configuration exists anywhere.

With seven backends the argument is stronger, not weaker: a Node proxy would need a route
table mirroring the Ingress, maintained in two places.

### Lifecycle

Stateless. Readiness = process is up. Any replica can be killed at any moment with no
in-flight state to lose. Rolling update, `maxUnavailable: 0`.

---

## 3. The seven request-serving services

All seven share one shape, described once here. Only their domain differs.

**Runs:** FastAPI/uvicorn, `Deployment`, 2 replicas, HPA on CPU/RPS (target ~70% CPU).

### The rule that shapes the two that touch AI

**Only `analogue` and `prediction` call Gemini on a request path, and only through
`ask_ai()` — plus `ingest`'s offline `CronJob`s.** Not the raw `google-genai` SDK — one
shared function in `shared/medstock_shared/ai.py`, so there is one place that holds the
retry/backoff logic and one place the cache lives, even though the network call itself now
happens inline, in each of those two services' own pods.

The `ingest` clause is the amendment [prognosis-and-procurement.md](prognosis-and-procurement.md)
§5.1 asked for, and it is a smaller concession than it looks. `ingest` extracts PP-3 risk
profiles from FDA label prose, per drug, on a schedule — so **no request ever waits on a
model**, and no patient data is anywhere near the call: the input is a public label. That is
the opposite trade from making a request-serving service an AI consumer, which is the thing
this rule exists to prevent. The two request-path callers are still exactly two.

`ingest` also runs with a longer retry budget than `ask_ai()`'s default, for the same reason:
offline, nobody is waiting, and a 503 that clears in seconds is worth waiting out rather than
degrading (`services/ingest/app/prognosis.py`).

The other five services have one external dependency: Postgres. `analogue` and `prediction`
have that same one plus Gemini directly — there used to be a second, internal dependency
(`ai-handler`) between them and the model; removing it took a hop out, at the cost each of
those two services now blocks its own request thread on a model call. §4 has the honest
accounting of what that costs.

### Request data flow

```
browser ──HTTPS──▶ Ingress ──▶ <service> pod
                                 │ 1. verify JWT locally → user, hospital_id, role
                                 │ 2. RBAC check (require("perm")) → 403 if not
                                 │ 3. BEGIN; SET LOCAL app.hospital_id, app.actor_id
                                 │ 4. query/mutate — no tenant filter written by hand
                                 │ 5. state change → trigger writes audit_log_entry
                                 │ 6. COMMIT
                                 ▼
                              Cloud SQL
```

### The seven

| Service | Owner | Ingress path | Responsibility | Endpoints (sketch) |
|---|---|---|---|---|
| `auth` | Tymur | `/api/auth` | Authenticate and authorize users; issue and rotate tokens. Holds the **private** signing key; everyone else holds the public one. | `POST /login` · `POST /logout` · `GET /me` |
| `inventory` | Pavlo | `/api/inventory` | Pharmacy availability per clinic / city / country. Owns the exposure query (`formulary × stock × shortage`) that the earlier sketch called `exposure-engine`. Resolves shelf rows from a clinical RxCUI by joining RxNorm NDCs to `stock_snapshot`. Shortage matrix (G1) is a read of the same join plus E2 trailing days. Purchase orders (F3/F4) and F1 writers live here. | `GET /stock?rxcui=` · `GET /items` · `GET /exposure` · `GET /shortages` · `GET /shortages/{id}/coverage` · `POST /formulary/import` (CSV) · `GET\|POST /orders` · `POST /recommendations` |
| `analogue` | Pavlo | `/api/analogue` | Drug identity (UC-1) plus therapeutic equivalents. Search turns a typed name into a `DrugIdentity` (RxCUI SCD/SBD); packages lists NDCs for that concept. Equivalents walk RxNorm, filter by indication/form/dose, price via NADAC, then `ask_ai()` ranks with a citation. Local availability is an overlay on `?facility_id=`. Also hosts the copilot gateway at `/api/copilot`. | `GET /drugs/search` · `GET /drugs/{rxcui}/packages` · `GET /analogues/{rxcui}` · `POST /api/copilot/messages` |
| `compliance` | Andrii | `/api/compliance` | Watch and validate pharmacy certificates; produce the audit export. Read-heavy, reads `audit_log_entry`, never writes it. | `GET /certificates` · `GET /audit` · `GET /export/compliance.csv` |
| `patient-profiling` | Andrii | `/api/patients` | Substitution safety for one patient (contraindications, allergies, interactions, label-derived prognosis), cohort demand and the PP-4 forecast, the demo patient CRUD behind the prescription cart, and the PP-5 queue where a pharmacist rules on extracted risk profiles. | `POST /assess` · `POST /demand` · `POST /forecast` · `GET /ruleset` · `GET /risk-profiles` · `POST /risk-profiles/{id}/review` · `GET\|POST /patients` · `GET\|PATCH /patients/{id}` · `POST /cart-check` |
| `prediction` | Mykhailo | `/api/prediction` | Predict usage, stock burn-down, future need. Days-of-supply is the core metric of the whole product. F1 restock recommendations are computed on read. | `GET /forecast/{rxcui}` · `GET /at-risk` · `GET /recommendations` |
| `warehouse` | Mykhailo | `/api/warehouse` | Warehouse structure (B1 facility registry), storage locations, stock placement, recorded consumption history, and storage-condition monitoring — hourly temperature/humidity telemetry checked against per-drug storage requirements, violations computed on read. F2 supplier catalog and quotes. G2 transfers. Also hosts the connector admin endpoints (planned). | `GET /facilities` · `GET /locations` · `GET /stock` · `GET /consumption` · `GET /locations/{id}/conditions` · `GET /excursions` · `GET /suppliers` · `GET /suppliers/{id}/catalog` · `POST /quote` · `POST /transfers` · `POST /connectors/{id}/propose-spec` (planned) |

Two of these — `analogue` and `prediction` — are AI consumers and call `ask_ai()` directly
(§4). The other five are ordinary CRUD-plus-query services with no path to Gemini at all.
`ingest` is the third caller but is not in this table: it is not a service, it is a set of
`CronJob`s (§7), and it calls the model offline rather than on anyone's request.

#### UC-1 — resolve a drug from a typed name

The front door for `GET /analogues/{rxcui}`, `GET /forecast/{rxcui}`, and `GET /stock?rxcui=`.
A physician or pharmacist types a name (`Aspirin 100 mg`); analogue queries live RxNorm
(NLM, keyless, from the service — not the browser), lifts ingredient/SCDC hits to `SCD`/`SBD`,
and returns candidates for **explicit** selection. A single hit is still a list; the client
must not auto-pick. Gemini is not involved.

Canonical clinical id is **RxCUI**. NDC is the shelf id, fetched later.

`GET /api/analogue/drugs/search?q=` — `drug:search`. `q` is 1–120 characters; `limit` defaults
to 20 (max 50).

```json
{
  "query": "Aspirin 100 mg",
  "items": [
    {
      "rxcui": "246461",
      "tty": "SCD",
      "name": "aspirin 100 MG Oral Tablet",
      "strength": "100 MG",
      "dose_form": "Oral Tablet",
      "in_formulary": true
    }
  ]
}
```

Sort: `in_formulary` desc, then RxNorm score. `in_formulary` is a left join to
`formulary_item.rxcui` for this hospital (B6 import / demo seed).

`GET /api/analogue/drugs/{rxcui}/packages` — `drug:search`. NDCs for the chosen concept
(step 2 of identity).

`GET /api/analogue/analogues/{rxcui}?facility_id=` — `drug:search` (+ `inventory:read` when
`facility_id` is sent). Ranked candidates; local availability is an overlay (C5).

`GET /api/inventory/stock?rxcui=&facility_id=` — `inventory:read`. Inventory asks the shared RxNorm client
for those NDCs, then returns matching `stock_snapshot` rows for the hospital (and facility, when
sent). Empty stock is an empty `items` list, not an error. On NLM failure the service matches
the query string as an NDC and sets `rxnorm_degraded: true`.

`GET /api/inventory/items?facility_id=` — `inventory:read`. The inventory table: on-hand rolled
up from `stock_batch`, status from `par_level` (B5), soonest expiry for the FEFO lot.

RxNorm is US English. Ukrainian trade names are out of scope (same capstone feed choice as §7).

`OPEN` — `patient-profiling` touches clinical data about identifiable people. Decide before
schema work whether the MVP stores any PHI at all, or only de-identified aggregates. Storing
none is a defensible MVP answer and removes a category of compliance argument you would
otherwise have to make on stage.

### Lifecycle (all seven)

| Phase | Behaviour |
|---|---|
| **Startup** | Read DB credentials + JWT public key from Secret Manager via Workload Identity → open connection pool → `/readyz` starts returning 200. **Does not run migrations.** |
| **Migrations** | A separate `Job` (`alembic upgrade head`) applied by CI *before* any Deployment rolls out. Fourteen pods (seven services × 2 replicas) racing on `alembic upgrade` is a corrupted schema. |
| **Steady state** | `/healthz` (liveness, process only — a database blip must not restart every pod) · `/readyz` (readiness, includes `SELECT 1`) |
| **Shutdown** | `preStop: sleep 5` so Ingress deregisters the endpoint first → SIGTERM → stop accepting, drain in-flight, close pool. `terminationGracePeriodSeconds: 30`. |
| **Node loss** (Spot preemption) | Kubernetes reschedules; the remaining replica absorbs traffic. This is the concrete reason for `replicas: 2` on Spot nodes. |

### Scaling

Request-bound for five of the seven — horizontal on CPU is honest there because the work is
JSON serialisation and Postgres round-trips.

`analogue` and `prediction` are the exception now, and it's worth being honest about it: a
Gemini call blocks the pod's request-handling capacity for the seconds it takes (cache misses
only — a hit returns immediately). CPU-target HPA does not see this the way it doesn't see it
for `ai_cache`'s replaced queue (§4) — a pod waiting on Gemini shows low CPU while still being
unable to take another request on that thread. At today's volume this is not yet a real
scaling problem; it is the thing to watch if traffic to those two services grows.

---

## 4. `ask_ai()` — calling Gemini without a queue

**Runs:** nowhere separately. `ask_ai()` is a plain function in
[`shared/medstock_shared/ai.py`](../shared/medstock_shared/ai.py), called synchronously from
inside `analogue` and `prediction`'s own request handlers. There used to be a ninth service —
`ai-handler` — holding a queue and a background dequeue loop; it has been removed. This
section is both the contract for `ask_ai()` and the record of that decision.

### Why the queue is gone

The queue's real benefits were crash-survival mid-call and a shared cache — and only the
second one was actually reachable without a separate service. A `SELECT`/`INSERT ON CONFLICT`
against a shared Postgres table gives the same cache and the same "one key, one place" win
that a whole Deployment, a NetworkPolicy, and an async HTTP contract were bought for. The
queue's other selling point — a pod restart mid-Gemini-call costs nothing — turned out to be
weaker in practice than it sounded: the caller (`analogue` or `prediction`) still had to poll
for up to 120 s and would surface a `503` to its own caller if that ran out, so the user-facing
outcome of a mid-call restart wasn't actually fixed, just relocated. What the queue *did* buy
— a hard ceiling on concurrent Gemini calls system-wide, and a scaling signal (queue depth)
decoupled from CPU — was real, but unused: replicas were fixed at 2 the whole time, never
KEDA'd. Paying for a Deployment, a NetworkPolicy, and an async contract for benefits that
weren't in use wasn't worth it at this system's volume (tens of calls/hour). If Gemini volume
or concurrency needs grow past what two callers' own replica counts can bound, that ceiling is
the concrete reason to bring a queue back — not "queues are more correct" in the abstract.

### Responsibility

1. **The mechanism** — the Gemini call, retry/backoff on 429/5xx, and the cache. Lives once,
   in `shared/`, so `analogue` and `prediction` don't each reinvent it.
2. **Nothing else.** `ask_ai()` does not know what a `recommendation` is. Task prompts,
   response schemas, and validators are registered by their owners in
   `shared/medstock_shared/ai_tasks.py` — unchanged by the queue's removal.

| Task type | Owner | Work | Output |
|---|---|---|---|
| `analogue` | Pavlo | rank therapeutic alternatives **+ the exact source sentence** | ranked items + citation |
| ~~`prediction`~~ | Mykhailo | ~~forecast days-of-supply from usage history~~ **retired** — see below | — |
| `extract` | Andrii | pull structured fields from shortage/label text | normalized fields + citation |
| `mapping_spec` | Mykhailo | propose a **declarative mapping spec** (never executable code) | spec, `status=awaiting_approval` |

**Amendment (issue #7):** the `prediction` AI task was retired before it was ever registered.
Spec E1 (docs/backend/specs/E1-demand-forecast.md) rules the forecast is not an LLM feature —
a model cannot produce a reproducible p10/p90, and the surge scenario (E3) and decision
provenance (H2) both depend on bit-identical replay. The service ships a deterministic
seasonal-naive quantile engine (`shared/medstock_shared/forecasting.py`) instead; `prediction`
makes no Gemini calls today, leaving `analogue` as the only request-path AI consumer.

Only `analogue` is actually registered in `shared/medstock_shared/ai_tasks.py` today —
`extract` and `mapping_spec` are the table above and a `# TASKS[…] — owner`
stub comment, nothing callable yet. `prediction` has real endpoints now (issue #7):
stored-run forecasts, days-of-supply/at-risk, and the server-side surge scenario, on dev port
:8006. Most other domain services still carry `/healthz` + `/readyz`
skeletons with business endpoints landing service by service; treat the "Endpoints (sketch)"
column in §3 as the plan, not the current state.

A recommendation is never auto-approved. `ask_ai()` produces candidates; only a pharmacist,
through `analogue`, produces a decision.

### The call

```python
from medstock_shared import ask_ai, AIError

try:
    result = ask_ai("analogue", {"rxcui": rxcui, "drug_name": name, "candidates": candidates,
                                  "source_text": shortage.source_text})
except AIError:
    raise HTTPException(503, "recommendation unavailable")
```

One function call, synchronous, no polling. `ask_ai` checks `ai_cache` first; on a miss it
calls Gemini, validates the result, writes it to the cache, and returns it — all inside the
same request. FastAPI runs a plain (non-`async def`) route in its threadpool, so this does not
block the event loop, but it does hold that request's thread for the duration of the call —
see §3 *Scaling* for what that costs.

### Deduplication is the cache — this part is unchanged from the old design

`dedupe_key = sha256(task + canonical_json(payload))` under a unique constraint on `ai_cache`.
Therefore:

- Two identical questions are one Gemini call; the second `ask_ai()` returns the cached result.
- **Anything volatile in a payload destroys this.** A `datetime.now()` in the payload means
  every call is a cache miss and a charge. To force a fresh answer, include something
  meaningful that changed — e.g. `shortage_event.updated_at`.
- Because the cache has no `hospital_id` (§1.1), two hospitals asking the identical question
  share the identical cached answer — a strictly bigger cache hit rate than the old per-job
  design had, since the payload never contained PHI to begin with.

### Talks to

- Cloud SQL (`ai_cache` read/write, from inside `analogue`/`prediction`'s own connection pool)
- Gemini (out, from `analogue`/`prediction` directly) — model is `GEMINI_MODEL` / `settings.gemini_model`; exponential backoff on 429/5xx, 3
  attempts, 20 s per-call timeout
- Cloud Logging (out)

No separate ingress, no separate egress point, no NetworkPolicy of its own — it inherits
whichever of the two callers made the call.

### What changed for `analogue` and `prediction` specifically

| | Before (`ai-handler`) | Now (`ask_ai()`) |
|---|---|---|
| Gemini key location | one Deployment | Secret on both `analogue` and `prediction` |
| Concurrency ceiling | `AI_MAX_CONCURRENCY=4`/pod, system-wide | none — bounded only by each service's own replica count and HPA |
| Failure on a slow/dead Gemini | job stays `pending`, retried by another pod | `AIError` after retries exhaust, `503` to the caller, in the same request |
| Blocks a request thread on the model call | no — caller polled a cheap status endpoint | yes — for the duration of a cache miss |

---

## 5. Lifecycle at a glance

| | `web` | 7 domain services |
|---|---|---|
| Kind | Deployment | Deployment |
| Replicas | 2 | 2 → HPA |
| Inbound | Ingress | Ingress |
| Outbound | — | Postgres (`analogue`, `prediction` also: Gemini) |
| Readiness probe | process up | `SELECT 1` |
| Grace period | 30 s | 30 s |
| Loses work if killed | no | in-flight request |
| Scale signal | fixed | CPU / RPS |

There is no separate row for AI work anymore — `analogue` and `prediction` are ordinary rows
in the "7 domain services" column, with one asterisk: a Gemini cache miss blocks that pod's
request thread for the call's duration (§3 *Scaling*, §4). A killed pod mid-call loses that one
in-flight request, same as any other in-flight request on this table — there is no reclaim
path anymore because there is no queue to reclaim from.

## 6. Failure modes

| Failure | Blast radius | Recovery |
|---|---|---|
| Gemini down | `analogue` and `prediction` degrade on cache misses; cached answers keep working | `ask_ai` raises `AIError` after retries exhaust → those two endpoints return 503; the other five services unaffected |
| `auth` down | Nobody can log in; **everyone already logged in keeps working** | Restart. This is the payoff of local JWT verification |
| Spot node preempted | 1–2 pods | Rescheduled; each service covered by its second replica; any in-flight request (including a Gemini call) is simply lost and retried by the client, same as any other request |
| Cloud SQL failover | Full outage, ~1 min | Pods reconnect via pool |
| Breaking change in `shared/` | **All seven services** | The distributed-monolith tax. Mitigate with the CI matrix: every service is built and tested on every PR |
| Bad model output (hallucinated citation) | One `ask_ai()` call | `validate()` raises before the result is cached or returned; the citation must be a verbatim substring of the source text |

## 7. What we gave up, knowingly

Dropping `worker` and `ingest` was not free. Neither came back as a queue-backed service —
`ingest` came back as three `CronJob`s (below); `worker`'s job (async LLM calls) did not come
back in any form — Gemini is now called inline, synchronously, from `analogue`/`prediction`
(§4). That trade is recorded in §4, not repeated here. What §4 does *not* cover:

**There is no scheduled process.** Nothing polls FDA, RxNorm, or NADAC. Nothing diffs
reference data to decide what changed. Nothing fans out work when a new shortage appears.
Concretely, this means today the system can answer "rank alternatives for this drug" but not
"tell me when a drug in my formulary enters shortage" — which is the product's core promise.

**Decided:** a new, minimal eighth image — `ingest` — not folded into `warehouse` or any other
domain service. Reference data (`drug`, `shortage_event`, `drug_price`, `rxnorm_edge`) has no
tenant owner (§1.1), so it gets no service owner either; bolting it onto whichever service
happens to host adjacent logic just re-creates the coupling the seven-service split was meant
to avoid. Three `CronJob`s off that one image (`shortages` hourly, `pricing` daily, `rxnorm`
weekly) rather than one job with `if hour % 24` logic inside — feeds do not share a cadence.
Explicitly **not** per-service polling: openFDA's 1 000 req/day quota is per IP, not per
service, so seven independent pollers would share one budget with no way to tell who spent it;
and reference data is identical for every hospital, so polling it once is why the cost curve is
sub-linear in site count (§1.1) — poll it seven times over and that stops being true.

Constraints it will have to respect, recorded now so they are not rediscovered late:

| Feed | Purpose | Constraint |
|---|---|---|
| FDA Drug Shortages | shortage events | keyless |
| openFDA Enforcement / Label | recalls, label text | keyless: 240 req/min, **1 000 req/day per IP** — the binding limit. One process paging in bulk stays under it; seven services each polling do not |
| RxNorm (NLM) | equivalence graph | keyless, ≤20 req/s requested. Near-static — cache in `rxnorm_edge`, refresh weekly |
| CMS NADAC | reference pricing | keyless; Socrata pagination, refreshed weekly upstream — daily polling is already generous |

Every write must be an upsert on a natural key and every enqueue `ON CONFLICT DO NOTHING`,
because a CronJob **will** run twice — a preempted Spot node, a manual re-trigger, a missed
schedule caught by `startingDeadlineSeconds`.

## 8. Open items

1. **Scheduled ingestion has no home.** §7. This blocks the product's core promise, not a
   side feature. **Decided and scaffolded:** `services/ingest` (three scripts,
   `deploy/k8s/ingest-cronjobs.yaml`) — not folded into an existing service, not polling spread
   across the seven domain services. Still open: the three `FEED_URL`s and field mappings are
   unverified placeholders (`ponytail:` comments in `services/ingest/app/*.py`), there is no
   migration for the four reference tables yet, and `rxnorm.py` has no real RXCUI seed list.
   Do not point the CronJobs at a live schedule until those are resolved.
2. **RLS policies on future tenant tables.** Wave 2 (`20260818_wave2_stock`) ENABLE/FORCE RLS
   plus `tenant_isolation` on every tenant table that exists today, including subquery
   policies on `storage_location` / `location_condition`. `session_scope()` sets
   `app.hospital_id` / `app.actor_id` / `app.actor_system` and `SET LOCAL ROLE app_role`
   so a docker/CI superuser cannot bypass FORCE RLS. Identity and reference tables stay
   exempt. Any new tenant table (`purchase_order`, `transfer_request`, …) must get a
   policy in the same migration that creates it. The app role must not own the tables.
   (`ai_cache` remains global on purpose — no `hospital_id`.)
3. **Stock data source for the MVP** — CSV, synthetic generator, or a self-written mock
   distributor API. Days-of-supply is the core metric and no public feed provides it. Blocks
   the `formulary_item` / `stock_snapshot` schema.
4. **User ↔ hospital cardinality** — one, or many via `membership`. Decided default: one per
   user *now*, but role stored in `membership` from day one so the change is a migration, not
   an auth rewrite.
5. **PHI in `patient-profiling`** — §3. Storing none in the MVP is defensible and cheaper.
6. **Token revocation** — short TTL only, no revocation list. A user whose hospital or role
   changes keeps the old claims until the token expires. Acceptable at MVP; state the TTL.
7. **Notification delivery** — in-app only for the MVP. Email/SMS would add a task type and a
   provider dependency.
8. **Physician surface** — a screen in our own web app, not an EHR/CPOE integration. State
   this plainly at defense rather than implying HL7 integration exists.
