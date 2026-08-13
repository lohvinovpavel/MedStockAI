# ingest

Not a service — no HTTP surface, no Deployment. Three standalone scripts, one
per feed, each run as its own `CronJob` off the `medstock-ingest` image
(see `deploy/k8s/ingest-cronjobs.yaml`):

| Script | Cadence | Writes |
|---|---|---|
| `app/shortages.py` | hourly | `shortage_event` |
| `app/pricing.py` | daily | `drug_price` |
| `app/rxnorm.py` | weekly | `rxnorm_edge` |

Run one by hand: `python -m app.shortages` (needs `DATABASE_URL`).

Every write is `INSERT ... ON CONFLICT DO UPDATE` on a natural key — a
CronJob **will** run twice (preempted Spot node, manual re-trigger, a missed
schedule caught by `startingDeadlineSeconds`), so re-running any script must
be a no-op on already-seen rows.

## TODO before this runs for real

The three `FEED_URL`s and field mappings in `app/*.py` are placeholders,
marked `ponytail:` inline — verify the actual FDA/CMS/RxNorm response shapes
against the live endpoints before wiring this into a schedule. `rxnorm.py`
also needs a real RXCUI seed list (formulary drugs), not the empty stub.

See `docs/services.md` §7/§8 for why this is a separate image instead of
logic inside `warehouse`.
