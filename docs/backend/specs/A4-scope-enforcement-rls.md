# A4 — Scope enforcement and row-level security

**Services:** all seven · **Flows:** every · **Status:** ⚠️ `PERMS` exists, RLS policies do not

## Goal

Two halves of one guarantee. The scope half exists: `require("inventory:read")` in
`shared/medstock_shared/auth.py`, with `PERMS` mapping role → scopes. The tenant half does
not: `models.py` states outright that there is "no application `WHERE hospital_id`" and that
RLS plus `session_scope` are meant to be the filter — but no policy has been created. Today a
mistake in one service's query leaks another hospital's rows.

## Current scope matrix

```python
PERMS = {
  "pharmacist": {"queue:read", "recommendation:approve", "inventory:read", "drug:search"},
  "physician":  {"alert:read", "inventory:read", "drug:search"},
  "director":   {"dashboard:read", "audit:read", "inventory:read", "drug:search"},
  "admin":      {"mapping:approve", "formulary:write", "audit:read", "inventory:read", "drug:search"},
}
```

## Scopes to add

| Scope | Roles | Used by |
|---|---|---|
| `facility:read` | all four | B1 |
| `batch:write` | pharmacist, admin | B4 |
| `par:write` | admin | B5 |
| `order:read` | pharmacist, director, admin | F4 |
| `order:write` | pharmacist, admin | F3 |
| `transfer:write` | pharmacist, director | G2 |
| `forecast:read` | pharmacist, physician, director | E1–E3 |
| `copilot:use` | all four | I1 |

`membership.role`'s CHECK constraint and the `PERMS` keys must stay in sync. A role present in
one and not the other fails closed (`PERMS.get(role, set())` → 403) — the right direction, but
a confusing outage. Add a startup assertion comparing the two.

## Row-level security

Every tenant table (see the registry in [db-schema.md](../db-schema.md)) gets:

```sql
ALTER TABLE stock_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_snapshot FORCE  ROW LEVEL SECURITY;   -- applies to the table owner too

CREATE POLICY tenant_isolation ON stock_snapshot
  USING      (hospital_id = current_setting('app.hospital_id', true)::uuid)
  WITH CHECK (hospital_id = current_setting('app.hospital_id', true)::uuid);
```

`WITH CHECK` matters as much as `USING`: without it a service can *insert* a row belonging to
another tenant even though it cannot read one back.

The `true` second argument makes `current_setting` return NULL when unset, and
`hospital_id = NULL` is never true — so a forgotten `session_scope` yields **zero rows, not
every row**. The system fails closed by construction.

No policy on identity tables (`hospital`, `app_user`, `membership`, `otp_challenge`) or
reference tables (`drug`, `rxnorm_edge`, `drug_price`, `shortage_event`, `drug_certification`,
`certification_finding`) — the documented two-class split in `docs/services.md` §1.1.
`ai_cache` is deliberately global as well.

## `session_scope`

One context manager in `shared/medstock_shared/db.py`, already the intended entry point:

```python
with session_scope(principal) as s:      # SET LOCAL app.hospital_id / app.actor_id
    ...
```

Use `SET LOCAL`, never `SET`: `LOCAL` scopes the setting to the transaction, so a pooled
connection cannot leak the previous request's tenant into the next one.

## Rules

1. Every endpoint declares a scope. A route with no `Depends(require(...))` is a review
   failure, not a default-open convenience.
2. No service writes `WHERE hospital_id = …` in application SQL. If a query seems to need it,
   the policy is missing.
3. Migrations run as the table owner; the application connects as `app_role` — which is why
   `FORCE ROW LEVEL SECURITY` is required, not just `ENABLE`.
4. `audit_log_entry` additionally carries `REVOKE UPDATE, DELETE FROM app_role` (see H1).
5. `hospital_id` is `uuid` on every tenant table (wave 0, migration
   `20260818_hospital_uuid`). RLS policies are still open — do not paper over
   that with an application `WHERE`.

## Acceptance criteria

- [ ] A test inserts as hospital A, opens a session as hospital B, and reads zero rows.
- [ ] A test writing a row with a mismatched `hospital_id` fails on `WITH CHECK`.
- [ ] A query run **without** `session_scope` returns zero rows rather than every row.
- [ ] Startup asserts `set(PERMS)` equals the roles in `membership`'s CHECK constraint.
- [ ] A route-table test enumerates every registered route and fails on a missing `require()`.

## Out of scope

Per-facility authorization (access to one clinic but not another). Scopes are hospital-wide;
add `facility_id` claims only when a customer asks for them.
