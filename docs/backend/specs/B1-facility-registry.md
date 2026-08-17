# B1 — Facility registry

**Service:** `warehouse` · **Flows:** 3, 7, 14, 16, 17 · **Status:** ✅ (issue #8, migration `20260817_warehouse`)
**Blocks:** B2, B4, B5, C5, E1, F3, G1, G2

> Implementation deviations: seed rows ship via `services/ingest/app/seed_demo.py`
> (`ENVIRONMENT=demo` guard, per `docs/demo-data.md` §5) rather than a migration (rule 5);
> `location_id` was not retyped — `stock_snapshot` gained a `facility_id` FK and
> `location_id` remains the intra-facility shelf code matching `storage_location.code`,
> with the stock natural key widened to `(hospital_id, ndc, facility_id, location_id)`.
> A `storage_location` table backs `GET /locations`, with `kind` driving condition
> monitoring (see backend-features B7). Cross-tenant 404 awaits the repo-wide RLS
> policies (A4).

## Goal

`stock_snapshot.location_id` is a bare `Text` column where `''` means "hospital-wide". The UI
already models six named sites with types and distances (`web/lib/mock-data.ts`), the sidebar
switches between the four operated ones, and eight other features key on `facility_id`. Give
the concept a table before anything else is built on top of it.

## API

### `GET /api/warehouse/facilities` — `facility:read`

`?operated=true` filters to switchable sites.

```json
{ "items": [
  { "id": 1, "code": "central", "name": "Central Hospital", "type": "Hospital",
    "operated": true, "lat": 50.45, "lon": 30.52 }
]}
```

### `GET /api/warehouse/facilities/{id}?from=<facility_id>` — `facility:read`

Adds `distance_km_from` computed against `from` when supplied.

### `GET /api/warehouse/locations?facility_id=` — `facility:read`

Storage locations inside a facility (ward fridge, shelf). A flat list keyed by `facility_id`;
the tree can wait until someone needs it.

## Data model

```sql
CREATE TABLE facility (
  id           bigserial PRIMARY KEY,
  hospital_id  uuid NOT NULL REFERENCES hospital(id),
  code         text NOT NULL,                 -- stable slug the web client sends
  name         text NOT NULL,
  type         text NOT NULL CHECK (type IN ('Hospital','Clinic','Pharmacy','Warehouse')),
  lat          numeric(9,6),
  lon          numeric(9,6),
  operated     boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (hospital_id, code)
);
```

In the same migration:

```sql
ALTER TABLE stock_snapshot ADD COLUMN facility_id bigint REFERENCES facility(id);
-- backfill: location_id '' → the tenant's Warehouse-type facility
-- location_id survives as an intra-facility shelf id and stops carrying facility identity
```

RLS policy per A4.

## Rules

1. `code` is what the web client sends; `id` is internal. Seed with the mock's slugs
   (`central`, `riverside`, `westend`, `warehouse-north`, plus the two partner sites) so the
   front end migrates without a translation table.
2. `operated = false` marks partner sites (St. Luke, Mercy) whose stock the shortage matrix
   displays but which never appear in the facility switcher.
3. Distance is **computed, not stored**. The mock's `distanceKm` is measured from Central,
   which is what produced the "Central Hospital · 0km away" bug when viewing from a clinic.
   Haversine over `lat`/`lon`, relative to the requesting facility.
   `ponytail: haversine in Python at six rows; PostGIS when this becomes a real geo query.`
4. A non-operated facility is never a valid target for `POST /orders`, `POST /batches`, or the
   `to_facility` of a transfer. Validate server-side, not in the UI.
5. Seed rows ship as a migration, not a fixture script — the demo needs them in every environment.

## Acceptance criteria

- [ ] `GET /facilities?operated=true` returns exactly the four switchable sites.
- [ ] `GET /facilities/2?from=3` reports a non-zero distance; `?from=2` reports 0.
- [ ] Every existing `stock_snapshot` row has a non-null `facility_id` after migration.
- [ ] A cross-tenant `GET /facilities/{id}` returns 404, not another hospital's row.
- [ ] `POST /api/inventory/batches` against a non-operated facility returns 422.

## Out of scope

Facility hierarchies (a warehouse as parent of clinics), opening hours, contact records,
per-facility user assignment.
