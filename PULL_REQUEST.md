# COMP-1 traffic light on the dashboard, PP-3 prognosis, and the data feeds behind them

Nine commits on top of `main` (`8e7b98b`). 28 files, +2228 / −77.

## What this does

Puts the FDA certification traffic light (COMP-1) on the dashboard inventory shelf, wires
label-derived risk profiles (PP-3) into `/assess`, and fixes the ingest side so both have
real data behind them.

## The two bugs worth reviewing first

**1. The daily job and the dashboard shared no drugs.**
`ingest-certification` has run daily since it was written, but it certifies `shelf_ndcs()`
— whatever is in `stock_snapshot` — and the dashboard shelf lives in `web/lib/mock-data.ts`.
The two sets had *zero* overlap. The job certified drugs nobody could see while every badge
on screen read `unknown`, and nothing anywhere errored.

`scripts/seed_stock.py` now seeds the ten dashboard NDCs, and
`services/compliance/tests/test_demo_shelf.py` pins the two lists together so they cannot
drift apart again silently.

**2. PP-3 was built but not connected.**
Extraction worked, the table had rows, the matcher worked when called directly — and
`/assess` called `assess()` without passing `risk_profiles`, so a label-derived prognosis
never reached an API response.

## COMP-1 on the shelf

The dashboard rendered certification from an invented `certStatus` field
(`valid` / `pending` / `expired`) that no backend produced, and the rows carried no NDC at
all — so the traffic light had nothing to join on.

Each of the ten rows now carries a real package NDC, resolved against the openFDA NDC
directory and matched on **both** generic and dosage form. Force-fitting an injection's
shortage onto a tablet row would have been inventing a finding, so where no genuine problem
exists for that drug in that form the row stays green.

Through the real rule engine the shelf evaluates to 4 green, 4 yellow, 2 red — none of it
chosen to produce a colour:

| status | drugs |
|---|---|
| green | amoxicillin/clavulanate, propofol, ceftriaxone, insulin glargine |
| yellow | albuterol (shortage), norepinephrine (Class II recall), azithromycin (to be discontinued), midazolam (shortage) |
| red | acetaminophen inj — Hikma, **Class I** recall, label mix-up · heparin — Baxter, **Class I** microbial + current shortage |

Quantities, batch numbers and burn rates stay invented. Reference data real, tenant data
synthetic, per `docs/demo-data.md`.

### The design call worth arguing about

**There is no fallback to the stored `certStatus` when compliance is unreachable.**

Falling back would render "valid" for a drug we could not check, which is exactly the
reassurance this feature exists to stop anyone giving. Unreachable is grey and says so.

Three states that used to be one:

- `unknown` — no FDA record for this NDC. This is what COMP-2 exploration is for.
- `unavailable` — we tried and could not reach the service.
- `green` — actually checked, actually clean.

A batch received as free text has no NDC, so it reads `unknown` rather than inheriting a
placeholder.

The certificate dialog replaces the mock PDF preview with the findings themselves: severity,
category, standing vs transient, the message, and a link to the FDA dataset that produced
it. Standing vs transient is what keeps the list actionable — a recall will clear, a dead
listing will not. It fetches only while open, because on a miss that endpoint triggers
COMP-2 exploration upstream and spends real request budget.

The KPI tile counts amber and red only. Folding `unknown` and `unavailable` into it would
make the number jump to the shelf size the moment compliance went offline, which is the
opposite of an alert.

The **audit page** is wired to the same source; it was reading the stored field and would
otherwise have shown a different colour than the shelf for the same drug.

## Ingest fixes

- **NADAC pricing was pointing at a dead endpoint.** Prices had never loaded. Now goes
  through the Medicaid metastore + datastore.
- **`_source.py` was missing `reraise=True`**, which made every `except httpx.HTTPStatusError`
  in the ingest package dead code. Retries are now limited to 429/5xx and transport errors.

## Supporting changes

- `StatusBadge` gains a `neutral` tone. "We do not know" is not a mild version of any
  existing tone and must not borrow the colour of one that came back clean. Required adding
  the key to the shortages `sortRank`, which types a `Record` over `StatusTone`.
- Images did not carry `scripts/`, so `seed_stock.py` could not run in-cluster at all.
  Added `COPY scripts` — same reasoning as migrations: any image can run them, none runs
  them on startup.
- `deploy/k8s/seed-stock-job.yaml`, a one-off Job. Deliberately **not** in
  `kustomization.yaml`, same as `seed-job.yaml` — seeding is something you decide to do, not
  something a rollout does to you.
- `next.config.mjs` proxies `/api/compliance` in dev on **8004** (patient-profiling took
  8003 on `main`). Without it the badge reads "unavailable" locally however healthy the
  service is.

## One thing the rebase broke, and the fix

`20260814_prog` (this branch) and `20260815_patient` (main) were both written
against `20260814_cert` as their parent. Each is fine alone; together they gave alembic
two heads, and `upgrade head` refused to guess — CI exited 255 before running a thing.

Prognosis is re-pointed onto patient, so the chain is linear: `cert → patient → prog`.
Prognosis moves because it is the unmerged one, while patient is already on main and may
be stamped in deployed databases. The tables are independent, so only the linearity
matters. The revision id stays `20260814_prog` despite now following an 08-15 revision —
alembic orders by the `down_revision` chain, not the name, and renaming an id already
stamped in dev databases to fix a cosmetic date would cost more than it buys.

## Verification

- **198 tests pass** (up from 188 on this branch before the rebase).
- **`next build` clean** — typecheck and ESLint, run via Docker.
- **`ruff check .` clean** repo-wide.
- **`alembic heads` returns exactly one**, and generating the upgrade offline runs every
  script in order and stamps through to `20260814_prog`.
- The drift guard **fails when mutated**, not merely passes today: changing one NDC in
  `mock-data.ts` fails with the NDC named and the consequence spelled out. It also asserts
  the UI list is non-empty, so a regex that silently stops matching cannot make the
  comparison pass by comparing two empty lists.

## To make the badges light up on a deployment

Run once per environment, after `migrate`:

```bash
kubectl -n medstock apply -f deploy/k8s/seed-stock-job.yaml
kubectl -n medstock wait --for=condition=complete job/seed-stock --timeout=300s
kubectl -n medstock create job certify-now --from=cronjob/ingest-certification
```

The last line certifies the freshly seeded shelf immediately rather than leaving the
dashboard grey until the 05:00 run.

## Known gaps, deliberately left

- `CopilotDrawer.tsx` still builds its certificate card from the mock `certStatus`. Wiring
  it means touching the copilot's mock conversation, which is outside COMP-1.
- `/cart-check` (new on `main`) calls `assess(vector, rxcui)` without `risk_profiles` — the
  same gap this PR fixes for `/assess`. Left alone rather than changing a teammate's new
  endpoint mid-rebase; worth a follow-up.
- I could not verify the remote GKE/Cloud SQL state: the local gcloud credentials are
  expired (`invalid_grant`). The Terraform and manifests are correct in the repo, but
  whether they are *applied* is unconfirmed.
