# Populating a new environment

`alembic upgrade head` creates every table **empty**. No reference data is
committed to the repository, and that is deliberate — FDA listings, CPIC
guidelines and FAERS ratios change under us, so they are fetched by feeds rather
than frozen into git.

The consequence is the thing this page exists for: **a freshly migrated
environment shows an empty Prognosis Review queue, no pharmacogenomic findings,
no import-alert or warning-letter badges, and no population signal** — and the
weekly CronJobs will not fix that until their schedule comes round, which for
the Sunday and Monday jobs can be six days away.

Run the feeds once, by hand, after migrating.

---

## 1. What fills what

| Table | Filled by | Schedule | Key needed |
|---|---|---|---|
| `drug_certification`, `certification_finding` | `ingest-certification` | daily 05:00 | — |
| `import_alert` | `ingest-import-alerts` | Mondays 04:00 | — |
| `warning_letter` | `ingest-warning-letters` | Mondays 04:30 | — |
| `news_signal` | `ingest-news` | daily 06:30 | — |
| `pgx_guideline` (Tier 3) | `ingest-cpic` | Sundays 05:00 | — |
| `adr_signal` (Tier 1) | `ingest-faers` | Sundays 07:00 | — |
| `drug_risk_profile` (PP-3) | `ingest-prognosis` | Sundays 06:00 | **`GEMINI_API_KEY`** |
| `assessment_log` | written by `/assess` and `/cart-check` | on use | — |
| `patient` (demo) | `seed-patients-job.yaml` | every dev deploy | — |

`ingest-faers` depends on the formulary — it costs one openFDA call per drug, so
it needs a drug list rather than "everything". Seed stock before it or it has
nothing to ask about. `ingest-cpic` deliberately does **not** filter by
formulary: the whole CPIC set is ~250 rows, so filtering saves nothing and would
silently empty Tier 3 on an environment whose shelf does not happen to overlap
CPIC's ~40 covered drugs.

Only the prognosis feed needs a model. Everything else is keyless: openFDA,
CPIC, accessdata.fda.gov and a news index.

---

## 2. Run them once, in this order

```bash
kubectl -n medstock apply -f deploy/k8s/migrate-job.yaml
kubectl -n medstock wait --for=condition=complete job/migrate --timeout=300s

kubectl -n medstock apply -f deploy/k8s/seed-stock-job.yaml
kubectl -n medstock wait --for=condition=complete job/seed-stock --timeout=300s

# Demo patients for the prescribe cart. Invented people — see §6.
# deploy-dev.yml already runs this on every deploy; by hand it needs the auth
# seed to have run first, because it resolves the tenant by hospital name.
kubectl -n medstock apply -f deploy/k8s/seed-patients-job.yaml
kubectl -n medstock wait --for=condition=complete job/seed-patients --timeout=300s

for feed in certification cpic faers import-alerts warning-letters news prognosis; do
  kubectl -n medstock create job "now-$feed" --from="cronjob/ingest-$feed"
done
```

Order matters in one place only: `seed-stock` before `certification`, because
the certification pass certifies what is on the shelf and a shelf that is not
there yet certifies nothing. The rest are independent.

---

## 3. What "working" looks like afterwards

```sql
SELECT 'pgx_guideline'     t, count(*) FROM pgx_guideline
UNION ALL SELECT 'adr_signal',    count(*) FROM adr_signal
UNION ALL SELECT 'import_alert',  count(*) FROM import_alert
UNION ALL SELECT 'warning_letter',count(*) FROM warning_letter
UNION ALL SELECT 'drug_risk_profile', count(*) FROM drug_risk_profile;
```

For scale, one full local run produced 252 CPIC rows, 76 FAERS signals for a
single drug, 2 285 import-alert firms and 989 warning letters.

---

## 4. The queue will still look empty, and that is the gate working

`ingest-prognosis` writes every extracted profile as `awaiting_approval`. Until
a pharmacist rules on one, `approved_profiles()` returns nothing and **no PP-3
finding appears in any assessment** — by design
([prognosis-and-procurement.md](prognosis-and-procurement.md) §1.3, gate 3).

So after seeding you should expect:

- **Prognosis Review** — rows to review, accept rate `—` (not `0%`; nothing has
  been ruled on, and a rate over no decisions is unknown rather than zero).
- **An assessment** — Tier 1 and Tier 3 findings, but no `PROGNOSIS_RISK` until
  someone approves a profile.

That is the difference between "the feed did not run" and "nobody has approved
anything yet", and the two look identical from the outside. Check the table
counts above before concluding a feed is broken.

---

## 5. Locally, without Kubernetes

Warehouse, forecasts, and the facility switcher need the demo tenant **before** the
ingest feeds. Without `seed_demo` those screens are empty even if alembic has run.
`seed_demo` + `seed_stock` also plant `stock_batch` / `par_level` for the 11
dashboard SKUs (wave 2 inventory table).

```bash
export DATABASE_URL=postgresql+psycopg://medstock:medstock@localhost:5432/medstock
# this repo uses `.venv/bin/python`; `uv` is optional
.venv/bin/alembic upgrade head

SEED_PASSWORD=devpassword123 (cd services/auth && ../../.venv/bin/python -m app.seed)
ENVIRONMENT=demo (cd services/ingest && ../../.venv/bin/python -m app.seed_demo)
.venv/bin/python scripts/seed_stock.py
.venv/bin/python scripts/seed_certification.py
.venv/bin/python scripts/seed_patients.py --count 200

cd services/ingest
../../.venv/bin/python -m app.cpic
../../.venv/bin/python -m app.faers --formulary --limit 25
../../.venv/bin/python -m app.import_alerts
../../.venv/bin/python -m app.warning_letters
../../.venv/bin/python -m app.news --shelf
../../.venv/bin/python -m app.prognosis --formulary --limit 20   # needs GEMINI_API_KEY
```

Both scrapers take `--dry-run`, which parses and reports without writing —
worth using first, because a layout change upstream shows up there as zero rows
rather than as an empty table nobody questions.

**`GEMINI_API_KEY`, not `GOOGLE_API_KEY`.** `ai.py` passes
`settings.gemini_api_key` explicitly, so a key under the other spelling leaves
it empty and every `ask_ai()` call fails with no useful message. See
`.env.example`.


---

## 6. The demo patients, and the line they sit on

`patient` is the one table in this system that stores a name and a date of
birth — the documented PHI exception for the prescribe cart
([phi-readiness.md](phi-readiness.md)). Everything downstream of it is fed a
de-identified vector built at `/cart-check` time, and that boundary is the whole
PHI argument.

**So the seed is invented people and must stay invented.** Do not load real
patients into a demo environment to make a screen look fuller. If a real
integration is ever needed, the vector contract is the thing to implement, not
this table.

### Which tenant the rows land in

The seed resolves the hospital **by name** (`St Mary's General`, matching
`services/auth/app/seed.py` and `seed_demo`) and exits non-zero if no such hospital exists.

That is not defensiveness for its own sake. This script used to default to the literal
`00000000-0000-0000-0000-000000000001` — seed_demo's DEMO GENERAL HOSPITAL, an id auth
never minted. So the old default wrote its rows, printed `seeded 1008`, and left every
user staring at an empty picker, because a user only ever sees the hospital named in their
token. Wave 0 collapsed those two names onto one uuid FK. Run the auth seed first, or
pass `--hospital-id`.

### The population

Eight curated patients, plus whatever `SEED_PATIENT_COUNT` asks for (1000 in
the Job). The generated cohort is not padding: `/demand`, the PP-4 forecast and
the population panels all answer questions about a cohort, and a cohort of eight
makes every one of them read as noise. It is deterministic — same seed, same
people — so a rebuilt environment is comparable to the one before it.

Frequencies are chosen to be plausible rather than authoritative, and roughly
45% of the cohort has **no genotype on file**. That is deliberate: a fully
genotyped population would make Tier 3 look far more useful than it is before a
hospital invests in testing.

The eight curated patients carry deliberately contrasting genotypes, because
that contrast *is* the Tier 3 demonstration:

| Patient | Phenotype | Citalopram in the cart |
|---|---|---|
| Elena Vasquez | `CYP2C19:Poor Metabolizer` | **amber**, score 40, CPIC level A recommendation quoted |
| Marcus Chen | `CYP2C19:Normal Metabolizer` | **green**, score 0, "CPIC advises standard dosing" |

Marcus getting an explicit *standard dosing* line rather than silence is the
point: it is how a reader tells "we checked the genotype and it is ordinary"
from "nobody looked".

Re-running the seed is safe. It pre-fetches the existing `(name, DOB)` keys in
one query, adds only what is missing, and backfills a genotype onto any patient
seeded before `pgx_phenotypes` existed — so an environment stood up earlier
upgrades rather than staying half-configured, and a no-op re-run costs a second.

Because it is safe, `deploy-dev.yml` runs it on **every** deploy rather than
behind a checkbox like the user seed. The distinction is what ends up in the
log: this job prints a count, the user seed prints a generated password.
