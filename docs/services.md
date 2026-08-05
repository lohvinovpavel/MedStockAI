# MedStockAI — Service Specification

Companion to [architecture.html](architecture.html). Per-process responsibilities, data flow, and lifecycle.

Status: **draft** — decisions marked `OPEN` are not settled.

---

## 0. Preface: four processes, two images

The proposal sketched five services. Three of them collapse:

| Sketch box | Reality |
|---|---|
| `ingest-svc` | Scheduled process — a `CronJob`, not a long-running service |
| `exposure-engine` | A SQL query (`formulary × stock × shortage`), executed inside `api` |
| `connector-factory` | Two admin endpoints in `api` + one job type in `worker` |
| `match-svc` | The `worker` process |
| `review-svc` | A router in `api` |

What actually gets deployed:

```
image: medstock-web   →  web       (Deployment)
image: medstock-app   →  api       (Deployment, cmd: uvicorn app.main:app)
                      →  worker    (Deployment, cmd: python -m app.worker)
                      →  ingest    (CronJob,    cmd: python -m app.ingest)
```

**One Python image, three commands.** CI builds once; the three workloads differ only by
`command` and resource requests. This matters for defense: the boundary between `api` and
`worker` is a *runtime* boundary (different load profile, different failure mode), not a
codebase boundary. Splitting the repo would buy nothing and cost a shared-model package.

---

## 1. Shared foundations

Everything below assumes these four mechanisms. They are described once because all three
Python workloads depend on them identically.

### 1.1 Two classes of table

| Class | Tables | RLS | Written by |
|---|---|---|---|
| **Reference** (global, shared by all hospitals) | `drug`, `shortage_event`, `drug_price`, `rxnorm_edge` | no | `ingest` only |
| **Tenant** (owned by one hospital) | `formulary_item`, `stock_snapshot`, `job`, `recommendation`, `review_decision`, `audit_log_entry`, `mapping_spec` | **yes** | `api`, `worker` |

The split is load-bearing. Reference data is polled once for all hospitals — this is why the
cost curve is sub-linear in site count. Tenant data never leaves its hospital, enforced by
row-level security, not by application code.

### 1.2 Tenant context is set, never filtered

Every process opens a transaction and declares who it is acting as before touching tenant
tables:

```sql
SET LOCAL app.hospital_id = '…';
SET LOCAL app.actor_id    = '…';
```

- `api` reads both from the verified JWT.
- `worker` reads `hospital_id` from the job row and sets `actor_id = 'system:worker'`.
- `ingest` sets them per hospital when enqueuing, and touches reference tables outside any
  tenant context.

`SET LOCAL` is transaction-scoped, so nothing leaks across pooled connections. The
application connects as a role **without** `BYPASSRLS` and which does not own the tables —
a role that owns a table ignores its own policies.

Consequence worth stating out loud: **the background path is not a privilege backdoor.**
A bug in `worker` cannot read another hospital's formulary any more than a bug in `api` can.

### 1.3 The audit log writes itself

The audit trail is the product's compliance value; "we remember to call `audit()`" is the same
weak guarantee as "we remember to write `WHERE hospital_id`". So it is a trigger, reading the
actor from the same session settings:

```sql
CREATE TRIGGER audit_review_decision
  AFTER INSERT OR UPDATE ON review_decision
  FOR EACH ROW EXECUTE FUNCTION write_audit_entry();
-- write_audit_entry() reads current_setting('app.actor_id') and current_setting('app.hospital_id')

REVOKE UPDATE, DELETE ON audit_log_entry FROM app_role;
```

Append-only is a **grant**, not a convention. This is demonstrable in ten seconds at defense.

### 1.4 The job table is the only queue

```sql
CREATE TABLE job (
  id           bigserial PRIMARY KEY,
  hospital_id  uuid NOT NULL,
  type         text NOT NULL,        -- match | extract | mapping_spec
  dedupe_key   text NOT NULL,
  payload      jsonb NOT NULL,
  status       text NOT NULL DEFAULT 'pending',  -- pending|running|done|failed
  attempts     int  NOT NULL DEFAULT 0,
  locked_at    timestamptz,
  last_error   text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (type, dedupe_key)
);
CREATE INDEX ON job (created_at) WHERE status IN ('pending','running');
```

No Redis, no Pub/Sub. At the volume this system produces — tens of jobs an hour — a broker is
an extra pod, an extra failure mode, and an extra thing to explain. The `UNIQUE (type,
dedupe_key)` constraint is what makes producers idempotent: a CronJob that runs twice inserts
the same key twice and the second insert is a no-op (`ON CONFLICT DO NOTHING`).

---

## 2. `web` — user interface

**Runs:** Next.js, `Deployment`, 1–2 replicas, no autoscaling.

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
  `api` **directly** on the same origin (`/api/*` routed by Ingress to the `api` Service).

That last point is a deliberate choice: routing API traffic *through* the Next.js server
would add a hop, force token forwarding through Node, and make `web` a availability
dependency of every data request. Same-origin means the session cookie is `httpOnly`,
`Secure`, `SameSite=Lax` and no CORS configuration exists anywhere.

### Lifecycle

Stateless. Readiness = process is up. Any replica can be killed at any moment with no
in-flight state to lose. Rolling update, `maxUnavailable: 0`.

---

## 3. `api` — synchronous request handling

**Runs:** FastAPI/uvicorn, `Deployment`, 2 replicas, HPA on CPU/RPS (target ~70% CPU).

### Responsibility

Everything a human is waiting on. Grouped by router:

| Router | Endpoints (sketch) | Permission |
|---|---|---|
| `auth` | `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | — |
| `queue` | `GET /recommendations?status=awaiting_review`, `GET /recommendations/{id}` | `queue:read` |
| `review` | `POST /recommendations/{id}/approve\|reject\|edit` | `recommendation:approve` |
| `alerts` | `GET /alerts?rxcui=…` — physician lookup at order time | `alert:read` |
| `dashboard` | `GET /dashboard/risk`, `GET /dashboard/savings`, `GET /export/compliance.csv` | `dashboard:read` / `audit:read` |
| `formulary` | `POST /formulary/import` (CSV multipart), `GET /formulary` | `formulary:write` |
| `connectors` | `POST /connectors/{id}/propose-spec`, `POST /mapping-specs/{id}/approve` | `mapping:approve` |

### The rule that shapes this service

**`api` never calls the LLM.** Not once, on any path.

A request that needs model output enqueues a job and returns `202` with a job id; the UI
polls. This includes the connector-factory spec proposal, which is the one place a human is
genuinely waiting on a model — it is still a job, because the alternative is a request thread
blocked for 30 seconds and an HPA whose CPU metric means nothing.

The payoff: `api` has one external dependency (Postgres), a p99 in the tens of milliseconds,
and an outage of the LLM provider degrades the system to "no new recommendations" rather than
"the application is down".

### Request data flow

```
browser ──HTTPS──▶ Ingress ──▶ api pod
                                 │ 1. verify JWT → user, hospital_id, role
                                 │ 2. RBAC check (permission in PERMS[role]) → 403 if not
                                 │ 3. BEGIN; SET LOCAL app.hospital_id, app.actor_id
                                 │ 4. query/mutate — no tenant filter written by hand
                                 │ 5. state change → trigger writes audit_log_entry
                                 │ 6. COMMIT
                                 ▼
                              Cloud SQL
```

Object-level authorization is a side effect of step 3, not separate code: a recommendation id
belonging to another hospital is simply not visible, and the handler returns `404`. There is
no "is this mine?" check to forget.

### Lifecycle

| Phase | Behaviour |
|---|---|
| **Startup** | Read DB credentials + LLM key from Secret Manager via Workload Identity → open connection pool → `/readyz` starts returning 200. **Does not run migrations.** |
| **Migrations** | A separate `Job` (`alembic upgrade head`) applied by CI *before* the new Deployment rolls out. Two `api` replicas racing on `alembic upgrade` is a corrupted schema. |
| **Steady state** | `/healthz` (liveness, process only) · `/readyz` (readiness, includes `SELECT 1`) |
| **Shutdown** | `preStop: sleep 5` so Ingress deregisters the endpoint first → SIGTERM → stop accepting, drain in-flight, close pool. `terminationGracePeriodSeconds: 30`. |
| **Node loss** (Spot preemption) | Kubernetes reschedules; the remaining replica absorbs traffic. This is the concrete reason for `replicas: 2` on Spot nodes. |

### Scaling

Request-bound. Horizontal on CPU is honest here because the work is JSON serialisation and
Postgres round-trips.

---

## 4. `worker` — background processing

**Runs:** same image, `python -m app.worker`, `Deployment`, 1–3 replicas.

**Has no Service object.** Nothing calls it. It only reaches out.

### Responsibility

Every operation that is slow, external, or non-deterministic.

| Job type | Input | Work | Output |
|---|---|---|---|
| `extract` | raw shortage/label text | LLM pulls structured fields **+ the exact source sentence** | normalized `shortage_event` fields + citation |
| `match` | `(shortage_event_id, formulary_item_id)` | walk RxNorm equivalence → filter by indication/form/dose → price via NADAC → LLM ranks with justification | N × `recommendation` rows, `status=awaiting_review` |
| `mapping_spec` | sample supplier payload | LLM proposes a **declarative mapping spec** (never executable code) | `mapping_spec` row, `status=awaiting_approval` |

A recommendation is never auto-approved. `worker` produces candidates; only a pharmacist in
`api` produces a decision.

### The dequeue loop

```sql
UPDATE job SET status = 'running', locked_at = now(), attempts = attempts + 1
WHERE id = (
  SELECT id FROM job
  WHERE attempts < 5
    AND (status = 'pending'
         OR (status = 'running' AND locked_at < now() - interval '10 minutes'))
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *;
```

One query does three things: dequeues, reclaims work abandoned by a crashed worker, and caps
retries. There is no separate reaper process — the stale-`running` branch *is* the reaper.

The transaction is **not** held across the LLM call. The loop is:

1. `UPDATE … RETURNING` → commit immediately. Job is now marked `running`.
2. `SET LOCAL app.hospital_id` from the job row.
3. Call the LLM. Seconds. No lock held, no transaction open.
4. New transaction: write results, `status='done'`, commit.
5. On exception: write `last_error`, `status='pending'` (or `failed` when `attempts >= 5`).

Holding a row lock across a multi-second network call would mean long-lived transactions,
bloated visibility horizons, and a vacuum problem — for no benefit, since step 1's
`locked_at` already provides the mutual exclusion.

### Crash safety

If a `worker` pod is preempted mid-LLM-call, the job sits in `running` with a stale
`locked_at` and another replica reclaims it after 10 minutes, `attempts` now higher. Nothing
is lost and nothing needs a broker's ack semantics. After five attempts the job lands in
`failed` and surfaces in the admin view with `last_error`.

Jobs are idempotent at the write step: `recommendation` carries
`UNIQUE (shortage_event_id, formulary_item_id, candidate_drug_id)`, so a job that is
reclaimed after having partially succeeded re-writes the same rows rather than duplicating
them.

### Talks to

- Cloud SQL (in/out)
- LLM API (out) — bounded concurrency, exponential backoff on 429/5xx, hard per-call timeout
- Cloud Logging (out)

Nothing inbound. No listening socket at all.

### Scaling — the load-profile argument

This is where the Kubernetes justification actually lives, so it needs to be exact.

`worker` is **latency-bound, not CPU-bound**: a replica spends most of a job blocked on an
LLM response. CPU-target HPA therefore does not work — a saturated worker with 40 queued
jobs and one in flight shows near-zero CPU. The correct signal is **queue depth**.

Recommended: **KEDA** with the PostgreSQL scaler.

```yaml
triggers:
  - type: postgresql
    metadata:
      query: "SELECT count(*) FROM job WHERE status = 'pending'"
      targetQueryValue: "5"
```

`minReplicaCount: 1`, `maxReplicaCount: 3` (bounded by LLM spend, not by nodes).

`OPEN` — KEDA is an extra operator to install and learn. Fallback if it eats more than half a
day: fix `replicas: 2`, keep the KEDA manifest in the repo unapplied, and demonstrate elastic
behaviour with `kubectl scale` against a seeded queue. Weaker on stage, but honest, and the
architectural point (independent scaling of an asymmetric workload) still stands.

Load arrives in **bursts**, not evenly: FDA publishes a batch and forty formulary items become
at-risk in the same minute. This is precisely the shape `docker-compose` cannot serve — it
scales the host, not the one saturated process.

### Lifecycle

| Phase | Behaviour |
|---|---|
| **Startup** | Secrets → pool → begin polling. No readiness gate needed (no traffic to gate). |
| **Steady state** | Poll every 5 s when idle; immediately re-poll after a successful job. |
| **Shutdown** | SIGTERM → finish the current job, then exit. `terminationGracePeriodSeconds: 120` (an LLM call plus its write). If the grace period is exceeded and the pod is SIGKILLed, the stale-`locked_at` path recovers it. |

---

## 5. `ingest` — scheduled acquisition

**Runs:** same image, `python -m app.ingest`, `CronJob`, hourly.

```yaml
schedule: "17 * * * *"          # off the hour — everyone else polls at :00
concurrencyPolicy: Forbid        # a slow run must not overlap the next
startingDeadlineSeconds: 300
activeDeadlineSeconds: 900
backoffLimit: 2
successfulJobsHistoryLimit: 3
failedJobsHistoryLimit: 3
```

### Responsibility

Pull the outside world in, and decide what changed.

Per run, per feed:

1. **Fetch** — conditional request (`If-None-Match` / `If-Modified-Since`) where the source
   supports it; a `304` costs nothing and skips the rest.
2. **Normalize** — vendor payload → canonical reference row.
3. **Upsert** — `ON CONFLICT (source, source_id) DO UPDATE`, natural keys only, never
   surrogate ids from the feed.
4. **Diff** — compare against the previous state; a shortage that was already known and
   unchanged produces no downstream work.
5. **Fan out** — for each *newly* at-risk formulary item across all hospitals, insert a
   `match` job with `dedupe_key = f"{shortage_event_id}:{formulary_item_id}"`.
6. **Record** — one `feed_run(source, started_at, status, items_seen, items_changed)` row per
   feed, which is what the ops dashboard reads.

Finally: flip abandoned jobs (`status='running' AND attempts >= 5 AND locked_at` older than an
hour) to `failed`. One statement; the only bookkeeping the worker loop cannot do itself.

### Sources and their constraints

| Feed | Purpose | Constraint |
|---|---|---|
| FDA Drug Shortages | shortage events | keyless |
| openFDA Enforcement / Label | recalls, label text | keyless: 240 req/min, **1 000 req/day per IP** — the binding limit; page in bulk, don't loop per drug |
| RxNorm (NLM) | equivalence graph | keyless; requested ≤20 req/s. Near-static — cache the graph in `rxnorm_edge` and refresh weekly, not hourly |
| CMS NADAC | reference pricing | keyless; Socrata pagination, refreshed weekly upstream — hourly polling is waste, run it daily |

`OPEN` — the four feeds do not share a natural cadence. Cleanest resolution is three
CronJobs off the same image (`shortages` hourly, `pricing` daily, `rxnorm` weekly) rather than
one job with internal `if hour % 24` logic. Three CronJob manifests is less code than one
scheduler written in Python, and each gets its own retry and history.

### Failure isolation

Feeds fail independently and must not take each other down. Each feed runs inside its own
`try/except`; a failure records `feed_run.status = 'error'` and continues. The Job exits
non-zero only if **every** feed failed — so `backoffLimit` retries a real outage rather than
one flaky endpoint.

### Idempotency

The CronJob **will** run twice — a preempted Spot node during a run, a manual re-trigger, a
missed schedule caught by `startingDeadlineSeconds`. Every write is an upsert on a natural
key, and every enqueue is `ON CONFLICT (type, dedupe_key) DO NOTHING`. A duplicate run
produces zero duplicate rows and zero duplicate LLM spend. This is what "idempotent polling"
in the proposal has to mean concretely.

### Not `ingest`'s job

Hospital CSV import arrives through `api` (`POST /formulary/import`), because a human is
attached to it and needs synchronous validation feedback. `ingest` never receives inbound
traffic.

---

## 6. Lifecycle at a glance

| | `web` | `api` | `worker` | `ingest` |
|---|---|---|---|---|
| Kind | Deployment | Deployment | Deployment | CronJob |
| Replicas | 1–2 | 2 → HPA | 1–3 → KEDA | 1 per run |
| Inbound | Ingress | Ingress | **none** | **none** |
| Outbound | — | Postgres | Postgres, LLM | Postgres, 4 public APIs |
| Readiness probe | process up | `SELECT 1` | n/a | n/a |
| Grace period | 30 s | 30 s | 120 s | `activeDeadlineSeconds` 900 s |
| Loses work if killed | no | in-flight request | no — reclaimed after 10 min | no — next run re-upserts |
| Scale signal | fixed | CPU / RPS | **queue depth** | schedule |

## 7. Failure modes

| Failure | Blast radius | Recovery |
|---|---|---|
| LLM provider down | No new recommendations | Jobs retry with backoff; existing queue stays reviewable; `api` unaffected |
| Spot node preempted | 1–2 pods | Rescheduled; `api` covered by second replica; in-flight job reclaimed |
| Cloud SQL failover | Full outage, ~1 min | Pods reconnect via pool; jobs retry |
| One public feed 5xx | That feed stale | Other feeds proceed; `feed_run` records it; visible on ops dashboard |
| openFDA daily quota exhausted | Recall data stale until reset | Bulk pagination + conditional requests keep normal usage well under; alert on `feed_run.status` |
| Bad LLM output (hallucinated citation) | One recommendation | Pharmacist rejects; citation is required to be a verbatim substring of source text — validated in code before the row is written, not trusted |
| Worker crash-loops on one job | That job | `attempts` cap → `failed` after 5, surfaced with `last_error` |

## 8. Open items

1. **Stock data source for the MVP** — CSV, synthetic generator, or a self-written mock
   distributor API. Days-of-supply is the core metric and no public feed provides it. Blocks
   the `formulary_item` / `stock_snapshot` schema.
2. **User ↔ hospital cardinality** — one, or many via `membership`. Decided default: one per
   user *now*, but role stored in `membership` from day one so the change is a migration, not
   an auth rewrite.
3. **KEDA or fixed replicas** — see §4.
4. **Auth** — own JWT vs. external IdP.
5. **Notification delivery** — in-app only for the MVP. Email/SMS would add a job type and a
   provider dependency; not required, since four live integrations already exist.
6. **Physician surface** — a screen in our own web app, not an EHR/CPOE integration. State
   this plainly at defense rather than implying HL7 integration exists.
