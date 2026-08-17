# D3 — Compliance export

**Service:** `compliance` · **Flow:** 18 · **Status:** ❌ (specified in `docs/services.md` §3, not built)
**Depends on:** H1 · **Scope:** `audit:read` (director, admin)

## Goal

Flow 18's Export button fires a toast and produces nothing. The export is the artefact the
whole compliance story exists to produce: a file a regulator or auditor reads without needing
access to the application.

## API

### `GET /api/compliance/export/compliance.csv?from=&to=&ndc=&facility_id=` — `audit:read`

`text/csv`, `Content-Disposition: attachment`. Streamed, not assembled in memory.

Columns:

```
occurred_at,actor,actor_role,entity_type,entity_id,action,drug_name,ndc,
certification_status,ruleset_version,finding_codes,ai_dedupe_key,source
```

### `GET /api/compliance/audit?entity_type=&entity_id=&limit=&offset=` — `audit:read`

The on-screen trail behind flow 18, same rows, JSON.

## Rules

1. Read-only over `audit_log_entry`. `compliance` **never writes** the audit log
   (`docs/services.md` §3) — the trigger does.
2. `actor` resolves to an email for humans. When `actor_id` is null the actor is a pipeline:
   emit the source system (`ingest-certification`, `prediction-cronjob`, `copilot`) rather than
   a blank cell. An empty actor column is what makes an export unusable as evidence.
3. `ai_dedupe_key` is carried through for AI-attributed rows (H2). It is what lets an auditor
   ask "show me the exact model output behind this decision" and get an answer from `ai_cache`.
4. `certification_status` and `ruleset_version` are copied from `drug_certification` **as of the
   row's timestamp** where available, so a colour that has since changed does not rewrite
   history. Where no historical value exists, emit the current one and mark `source=current`.
5. Date range defaults to the last 90 days. An unbounded export is a denial-of-service on a
   real dataset.
6. Stream with a server-side cursor and `yield` rows; never `SELECT *` into a list.
7. CSV injection: prefix any cell beginning with `=`, `+`, `-` or `@` with a single quote.
   These files get opened in Excel — this is a real trust boundary, not a formality.

## Acceptance criteria

- [ ] Export of a 50,000-row range completes without the process RSS tracking row count.
- [ ] Every row has a non-empty actor column, human or system.
- [ ] A cell starting with `=` is neutralised in the output.
- [ ] Two exports of the same range are byte-identical (deterministic ORDER BY).
- [ ] Requesting a range outside the caller's tenant returns their rows only, via RLS.
- [ ] A `pharmacist` role gets 403 (`audit:read` is director/admin).

## Out of scope

PDF rendering, digital signature of the export, scheduled delivery to an archive bucket,
per-regulator column templates.
