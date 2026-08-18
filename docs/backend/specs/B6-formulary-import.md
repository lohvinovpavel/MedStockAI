# B6 — Formulary import

**Service:** `inventory` · **Flow:** 4 · **Status:** ✅ (wave 3, migration `20260818_wave3`) · **Scope:** `formulary:write` (admin; already in `PERMS`)

> Implementation: `GET /formulary` fills `name` from `demo_shelf` when known rather than
> calling live RxNorm per row (import rule 5 — NLM stays off the write/list path). UC-1
> search still sorts formulary hits first from the table.

## Goal

`formulary_item` exists and `analogue` already reads it to boost UC-1 search hits.
`POST /formulary/import` (admin) writes the table; the demo seed plants the dashboard
RxCUIs so a fresh environment is not empty. After import, `in_formulary` is true and
UC-1 search sorts those hits first.

## API

### `POST /api/inventory/formulary/import` — `formulary:write`

`multipart/form-data`, one `file` part, `text/csv`, max 5 MB / 10,000 rows.

```csv
rxcui,name
246461,aspirin 100 MG Oral Tablet
1049640,norepinephrine 1 MG/ML Injectable Solution
```

200:
```json
{ "received": 322, "inserted": 300, "updated": 22,
  "rejected": [ { "line": 41, "rxcui": "abc", "reason": "rxcui_not_numeric" } ] }
```

### `GET /api/inventory/formulary?q=` — `inventory:read`

### `DELETE /api/inventory/formulary/{rxcui}` — `formulary:write`

## Rules

1. `rxcui` is the only required column. `name` is advisory and is **not** stored — the
   canonical name comes from RxNorm at read time, so a stale CSV cannot rename a drug.
2. Upsert on `(hospital_id, rxcui)`, the existing unique constraint. Re-importing the same file
   is a no-op that only touches `updated_at`.
3. Import is **additive**. It never deletes rows absent from the file: a truncated upload would
   otherwise silently drop drugs from the formulary. Removal is the explicit `DELETE`.
4. Validate the whole file before writing any row — non-numeric rxcui, blank lines, duplicates
   within the file (keep the first, report the rest). One transaction; a partial import is
   worse than a failed one.
5. Do **not** verify each rxcui against live RxNorm during import. 300 sequential NLM calls in
   one request is how this endpoint times out. Unknown codes surface naturally as empty search
   results later.
6. Trust boundary — this is user-uploaded content. Enforce the size cap before parsing, the row
   cap during parsing, and reject a file whose first line is not a recognisable header. Do not
   echo raw cell contents back in error messages beyond the offending value.

## Acceptance criteria

- [x] Importing the same file twice reports 0 inserted the second time and leaves the row count unchanged.
- [x] A file with one bad row imports the rest and reports the offending line number.
- [x] A 6 MB upload is rejected before parsing begins.
- [x] A file with 10,001 rows is rejected with 422.
- [x] After import, `GET /api/analogue/drugs/search` sorts formulary hits first.
- [x] Import runs in a single transaction — a mid-file failure leaves zero rows written.

## Out of scope

Excel formats, per-facility formularies, therapeutic-class hierarchies, an approval workflow
for formulary changes, ATC code import.
