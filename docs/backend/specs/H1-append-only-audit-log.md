# H1 — Append-only audit log

**Owner:** Postgres itself — read through `compliance` · **Flows:** 8, 18 · **Status:** ✅ (wave 1, migration `20260818_h1_audit`)
**Blocks:** B4, D3, F1, F3, G2 · **Scope:** `audit:read`

> Implementation deviations: the trigger is attached **only** to `review_decision`.
> `purchase_order`, `stock_batch`, `transfer_request`, and `par_level` do not exist
> yet; `formulary_item` and `drug_certification` are written by seeds/`SessionLocal`
> without an actor, so the CHECK would abort those jobs. RLS FORCE is on these two
> tables only (the rest of A4 is wave 2). `GET /audit` SETs `LOCAL ROLE app_role`
> because docker/CI connect as a superuser that would otherwise bypass FORCE RLS.
> F1 writers are still open — the table exists so the trigger has a subject; the
> `/audit` timeline is empty until a recommendation is stored. Pharmacist holds
> `audit:read` to match `PAGE_ROLES` and the rbac matrix (D3 export stays director).

## Goal

`docs/services.md` §1.3 builds the compliance story on a trigger that writes to
`audit_log_entry` whenever `review_decision` changes. Append-only is a grant
(`REVOKE UPDATE, DELETE FROM app_role`), not an application `audit()` call.
Flow 18's timeline is served from this table.

## Data model

```sql
CREATE TABLE audit_log_entry (
  id             bigserial PRIMARY KEY,
  hospital_id    uuid NOT NULL,
  actor_id       uuid,                       -- NULL = a pipeline, not a person
  actor_system   text,                       -- 'ingest-certification', 'prediction-cronjob', 'copilot'
  entity_type    text NOT NULL,
  entity_id      text NOT NULL,
  action         text NOT NULL,              -- INSERT | UPDATE | DELETE | domain verb
  before         jsonb,
  after          jsonb,
  ai_dedupe_key  text,                       -- links to ai_cache (H2)
  occurred_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_entity ON audit_log_entry (hospital_id, entity_type, entity_id, occurred_at DESC);
CREATE INDEX ix_audit_time   ON audit_log_entry (hospital_id, occurred_at DESC);

CHECK (actor_id IS NOT NULL OR actor_system IS NOT NULL)
```

## The trigger

```sql
CREATE FUNCTION write_audit_entry() RETURNS trigger AS $BODY$
BEGIN
  INSERT INTO audit_log_entry (hospital_id, actor_id, actor_system, entity_type, entity_id,
                               action, before, after, ai_dedupe_key)
  VALUES (
    current_setting('app.hospital_id', true)::uuid,
    nullif(current_setting('app.actor_id', true), '')::uuid,
    nullif(current_setting('app.actor_system', true), ''),
    TG_TABLE_NAME,
    COALESCE(NEW.id, OLD.id)::text,
    TG_OP,
    CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
    CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END,
    nullif(current_setting('app.ai_dedupe_key', true), '')
  );
  RETURN COALESCE(NEW, OLD);
END;
$BODY$ LANGUAGE plpgsql;

CREATE TRIGGER audit_review_decision AFTER INSERT OR UPDATE ON review_decision
  FOR EACH ROW EXECUTE FUNCTION write_audit_entry();
```

Wave 1 attaches the trigger **only** to `review_decision`. Attach the same trigger to
`purchase_order`, `stock_batch`, `transfer_request`, `par_level`, `formulary_item`, and
`drug_certification` when those writers go through `session_scope` with an actor — seeds
that write formulary/certification without one would abort on the CHECK.

## Append-only is a grant

```sql
REVOKE UPDATE, DELETE ON audit_log_entry FROM app_role;
GRANT  INSERT, SELECT  ON audit_log_entry TO   app_role;
```

"We remember to call `audit()`" is the same weak guarantee as "we remember to write
`WHERE hospital_id`" — and with seven services it has to hold in seven codebases. A grant holds
in zero. This is demonstrable in ten seconds at defense: connect as `app_role`, try
`DELETE FROM audit_log_entry`, watch it fail.

## Rules

1. No service calls an audit function. If application code writes an audit row, the trigger is
   missing from that table.
2. `actor_id` and `actor_system` both come from session settings that `session_scope` (A4) sets
   with `SET LOCAL`. A CronJob sets `app.actor_system` and leaves `app.actor_id` empty.
3. A write with neither actor set violates the CHECK and **fails the business transaction**.
   That is intended: an unattributable change to regulated data should not commit.
4. `before`/`after` are whole-row JSONB. Storage is cheaper than a schema migration on the audit
   table every time a domain table gains a column.
5. Never log credentials, tokens, or `password_hash`. Do not attach this trigger to `app_user`
   without a column filter.
6. Retention: none for now. Deleting audit rows requires a documented policy, not a cron job
   someone adds quietly.

## Acceptance criteria

- [x] `DELETE FROM audit_log_entry` as `app_role` raises insufficient privilege.
- [x] `UPDATE` as `app_role` raises insufficient privilege.
- [x] Inserting a `review_decision` produces an audit row with no application code calling `audit()`. F1's approve endpoint is still open — until it writes this table the live trail stays empty.
- [x] A write inside `session_scope` with no actor configured rolls back the whole transaction.
- [x] A CronJob write produces a row with `actor_system` set and `actor_id` null.
- [x] Flow 18's timeline is served entirely from this table — no fixture data remains.

## Out of scope

Hash-chaining or tamper-evident sealing of rows, WORM storage export, log shipping to an
external SIEM, per-field redaction policies.
