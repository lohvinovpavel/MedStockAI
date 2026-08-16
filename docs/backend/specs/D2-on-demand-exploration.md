# D2 — On-demand certificate exploration

**Service:** `compliance` · **Flow:** 6 · **Status:** ✅ implemented

`POST /explore` in `services/compliance/app/main.py:172`. Evaluates an NDC the scheduled
CronJob never polled, calling openFDA live and writing a `drug_certification` row with
`provenance = 'on_demand'` and a TTL in `expires_at`.

The `provenance` column is the point: a Director export that cannot say where a colour came
from is not evidence. `scheduled` means a CronJob wrote it; `on_demand` means someone explored
it. Only `on_demand` rows carry a TTL, because nothing refreshes them.

**No implementation work.** Verify:

- [ ] An expired `on_demand` row is re-explored rather than served stale.
- [ ] An `on_demand` row is not overwritten by the scheduled job in a way that loses its
      provenance history.
- [ ] Rate limiting: this endpoint calls an external API on demand and is reachable by any
      authenticated user. Confirm it cannot be used to hammer openFDA.
