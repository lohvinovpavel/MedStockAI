"""Seed demo patients for the physician prescribe cart.

Demo PHI exception — stores name/DOB in `patient` for the capstone UI.
Maps to PatientVector only at /cart-check time.

**Everyone here is invented, and must stay invented.** Names are drawn from
fixed pools and combined by a seeded generator; nothing is derived from a real
person, and no real patient belongs in this table.

Two populations, on purpose:

* **Eight curated patients** with hand-written clinical stories. These are the
  demo script — each exercises a different part of the pipeline, and they stay
  put so a walkthrough says the same thing twice.
* **A generated cohort** behind `--count`, which is what makes the population
  features mean anything: `/demand` and the PP-4 forecast answer questions
  about a cohort, and a cohort of eight is a rounding error.

Deterministic, like `services/ingest/app/gen_demo.py` — the same seed produces
the same people, so a rebuilt environment is comparable to the one before it.

  uv run --no-sync python scripts/seed_patients.py --hospital-id <uuid>
  uv run --no-sync python scripts/seed_patients.py --count 1000
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "shared"))

from medstock_shared.db import SessionLocal
from medstock_shared.models import Hospital, Patient
from sqlalchemy import select

# `patient.hospital_id` is Text with no foreign key, so seeding into a hospital
# that does not exist succeeds and writes rows nobody can ever see — every user
# is scoped to the hospital in their token. This script used to default to the
# literal 00000000-0000-0000-0000-000000000001, which matches nothing: the only
# place a `hospital` row is created is services/auth/app/seed.py, and it lets
# Postgres generate the uuid. That default is how a demo ends up with a full
# table and an empty screen.
#
# Resolve by name instead, and refuse to guess.
DEFAULT_HOSPITAL_NAME = "St Mary's General"  # keep in step with auth's seed

# Invented people. Nothing here belongs to anyone — the `patient` table is the
# documented PHI exception for the prescribe demo (docs/phi-readiness.md), and
# it stays populated by generated data only. Never load real patients here.
#
# `pgx_phenotypes` is what makes Tier 3 reachable in the demo. Without a
# genotype on someone, stage 8 has nothing to match and the pharmacogenomic
# tier is built, correct and invisible — which is exactly how PP-3 shipped.
# The values are CPIC's own vocabulary; see PatientVector.pgx_phenotypes.
DEMO_PATIENTS = (
    {
        "full_name": "Elena Vasquez",
        "date_of_birth": date(1978, 4, 12),
        "blood_group": "A+",
        "allergy_codes": [],
        "condition_codes": ["avoid_caffeine"],
        # Tier 3, actionable. Prescribe an SSRI and CPIC's level A
        # recommendation fires with a verbatim quote.
        "pgx_phenotypes": ["CYP2C19:Poor Metabolizer"],
    },
    {
        "full_name": "Marcus Chen",
        "date_of_birth": date(1990, 11, 3),
        "blood_group": "O-",
        # Tier 0 hard gate: a penicillin allergy blocks amoxicillin outright
        # and produces no score at all.
        "allergy_codes": ["penicillin"],
        "condition_codes": [],
        # Normal on the same gene as Elena, deliberately. Same drug, two
        # patients, two answers — and his says "standard dosing" rather than
        # nothing, which is how a reader tells checked-and-fine from never-looked.
        "pgx_phenotypes": ["CYP2C19:Normal Metabolizer"],
    },
    {
        "full_name": "Doreen Whitfield",
        "date_of_birth": date(1946, 2, 27),
        "blood_group": "B+",
        "allergy_codes": [],
        # Heart failure is one of the four risk factors in metformin's boxed
        # warning, so she is the patient PP-3 was built to catch — once a
        # pharmacist approves the profile.
        "condition_codes": ["I50.9"],
        "pgx_phenotypes": ["CYP2D6:Poor Metabolizer"],
    },
    {
        "full_name": "Tomas Nowak",
        "date_of_birth": date(1955, 9, 8),
        "blood_group": "A-",
        "allergy_codes": ["sulfa"],
        "condition_codes": [],
        # G6PD deficiency: eight drugs in the loaded CPIC set carry a
        # recommendation for it, several of them ordinary antibiotics.
        "pgx_phenotypes": ["G6PD:Deficient"],
    },
    {
        "full_name": "Amara Okonkwo",
        "date_of_birth": date(1985, 6, 19),
        "blood_group": "O+",
        "allergy_codes": [],
        "condition_codes": [],
        # Ultrarapid rather than poor — the opposite failure mode, and the one
        # people forget: normal doses can overshoot.
        "pgx_phenotypes": ["CYP2D6:Ultrarapid Metabolizer"],
    },
    {
        "full_name": "Henry Ashfield",
        "date_of_birth": date(1934, 12, 1),
        "blood_group": "AB+",
        "allergy_codes": ["nsaid"],
        "condition_codes": [],
        # 90+. The age band is derived from the date of birth, so this is how
        # the stage 9 rules get a subject at all.
        "pgx_phenotypes": [],
    },
    {
        "full_name": "Priya Raghavan",
        "date_of_birth": date(1998, 3, 15),
        "blood_group": "B-",
        "allergy_codes": [],
        "condition_codes": [],
        # No genotype on file, and that is a case worth demonstrating: stage 8
        # stays silent rather than guessing, and `stages_completed` says so.
        "pgx_phenotypes": [],
    },
    {
        "full_name": "Ruth Delacroix",
        "date_of_birth": date(1961, 7, 22),
        "blood_group": "A+",
        "allergy_codes": [],
        "condition_codes": [],
        # Two genes at once. Each is matched independently — CPIC rows keyed on
        # a *pair* of genes are skipped by the feed, which is a documented gap
        # rather than something this patient papers over.
        "pgx_phenotypes": ["CYP2C9:Poor Metabolizer", "CYP2C19:Rapid Metabolizer"],
    },
)


# --- the generated cohort ----------------------------------------------------
# The frequencies below are chosen to be *plausible*, not authoritative. This is
# demo data, and the point is a population with realistic spread rather than a
# uniform one: a cohort where every phenotype is equally common makes the PP-4
# forecast look tidy in a way no real hospital ever is.

SEED = 20260818

_FIRST = [
    "Amara",
    "Priya",
    "Elena",
    "Marcus",
    "Doreen",
    "Tomas",
    "Henry",
    "Ruth",
    "Ana",
    "Ivan",
    "Leila",
    "Noah",
    "Mei",
    "Omar",
    "Sofia",
    "Jonas",
    "Fatima",
    "Diego",
    "Hana",
    "Yusuf",
    "Clara",
    "Viktor",
    "Nadia",
    "Samuel",
    "Rosa",
    "Pavel",
    "Aisha",
    "Marek",
    "Lucia",
    "Karim",
    "Greta",
    "Anton",
    "Mira",
    "Felix",
    "Zara",
    "Otto",
    "Ines",
    "Bruno",
    "Talia",
    "Emil",
]
_LAST = [
    "Vasquez",
    "Chen",
    "Whitfield",
    "Nowak",
    "Ashfield",
    "Delacroix",
    "Okonkwo",
    "Raghavan",
    "Kovac",
    "Silva",
    "Haddad",
    "Lindqvist",
    "Nakamura",
    "Ferreira",
    "Andersson",
    "Bauer",
    "Moreau",
    "Oyelaran",
    "Popescu",
    "Duarte",
    "Kaminski",
    "Ibrahim",
    "Novak",
    "Sorensen",
    "Rahman",
    "Castillo",
    "Weber",
    "Petrov",
    "Mbeki",
    "Larsen",
]

# Roughly Western-population blood group frequencies.
_BLOOD = (
    ("O+", 37),
    ("A+", 33),
    ("B+", 9),
    ("O-", 7),
    ("A-", 6),
    ("AB+", 3),
    ("B-", 2),
    ("AB-", 1),
)

# Phenotype prevalence varies widely by ancestry; these sit inside commonly
# quoted ranges. `None` — nothing on file — is deliberately the majority, since
# that is the honest state of most patients in most hospitals, and a cohort
# where everyone is genotyped would make Tier 3 look far more useful than it is
# before a hospital invests in testing.
_PHENOTYPES = (
    (None, 55),
    ("CYP2C19:Normal Metabolizer", 12),
    ("CYP2C19:Intermediate Metabolizer", 7),
    ("CYP2C19:Rapid Metabolizer", 4),
    ("CYP2C19:Poor Metabolizer", 3),
    ("CYP2D6:Normal Metabolizer", 6),
    ("CYP2D6:Intermediate Metabolizer", 4),
    ("CYP2D6:Poor Metabolizer", 3),
    ("CYP2D6:Ultrarapid Metabolizer", 1),
    ("CYP2C9:Intermediate Metabolizer", 2),
    ("G6PD:Deficient", 2),
    ("SLCO1B1:Decreased Function", 1),
)

_ALLERGIES = ((None, 82), ("penicillin", 10), ("sulfa", 5), ("nsaid", 3))
_CONDITIONS = ((None, 78), ("I50.9", 9), ("E11.9", 8), ("avoid_caffeine", 5))


def _pick(rng: random.Random, weighted: tuple):
    values, weights = zip(*weighted, strict=True)
    return rng.choices(values, weights=weights, k=1)[0]


def generated_patients(count: int, seed: int = SEED) -> list[dict]:
    """`count` invented people, reproducibly.

    Ages skew old on purpose. A hospital formulary is consumed mostly by older
    patients, and an age-flat cohort would make the PP-4 forecast — which turns
    on how many patients age out of a therapy — look far calmer than reality.
    """
    rng = random.Random(seed)
    people: list[dict] = []
    for i in range(count):
        # Roughly 40% born before 1961, 40% mid-life, 20% young.
        bucket = rng.choices(("old", "mid", "young"), weights=(40, 40, 20), k=1)[0]
        year = {
            "old": lambda: rng.randint(1930, 1960),
            "mid": lambda: rng.randint(1961, 1985),
            "young": lambda: rng.randint(1986, 2006),
        }[bucket]()

        phenotype = _pick(rng, _PHENOTYPES)
        allergy = _pick(rng, _ALLERGIES)
        condition = _pick(rng, _CONDITIONS)
        people.append(
            {
                # The index keeps names unique without a retry loop. The natural
                # key is (name, dob), and a 1 000-person cohort drawn from a
                # 40x30 pool would otherwise collide constantly — birthday
                # problem, not bad luck.
                "full_name": f"{rng.choice(_FIRST)} {rng.choice(_LAST)} #{i + 1:04d}",
                "date_of_birth": date(year, rng.randint(1, 12), rng.randint(1, 28)),
                "blood_group": _pick(rng, _BLOOD),
                "allergy_codes": [allergy] if allergy else [],
                "condition_codes": [condition] if condition else [],
                "pgx_phenotypes": [phenotype] if phenotype else [],
            }
        )
    return people


def resolve_hospital_id(session, explicit: str | None, name: str) -> str:
    """The tenant to seed into — named, not assumed.

    An explicit `--hospital-id` always wins; tests and one-off environments need
    to name a tenant that has no `hospital` row at all. Otherwise look the
    hospital up by name and, if it is not there, **stop**. Falling back to a
    constant here would put a thousand invisible rows in the table and report
    success, which is a worse outcome than a failed job.
    """
    if explicit:
        return explicit.strip()

    row = session.execute(select(Hospital).where(Hospital.name == name)).scalars().first()
    if row is None:
        raise SystemExit(
            f"no hospital named {name!r} — nothing to seed into. "
            "Run the auth seed first (python -m app.seed in services/auth, or "
            "deploy/k8s/seed-job.yaml), or pass --hospital-id explicitly."
        )
    return str(row.id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo patient profiles")
    parser.add_argument(
        "--hospital-id",
        default=None,
        help="tenant to seed into; defaults to whichever hospital is named by --hospital-name",
    )
    parser.add_argument(
        "--hospital-name",
        default=DEFAULT_HOSPITAL_NAME,
        help="resolve the tenant by name instead of by uuid",
    )
    parser.add_argument(
        "--count",
        type=int,
        # Env fallback so the Kubernetes Job can be re-pointed by changing a
        # value rather than by rewriting a command line in a manifest.
        default=int(os.environ.get("SEED_PATIENT_COUNT", "0")),
        help="generated patients to add on top of the eight curated ones",
    )
    parser.add_argument("--seed", type=int, default=SEED, help="same seed, same cohort")
    args = parser.parse_args()

    specs = list(DEMO_PATIENTS)
    if args.count > 0:
        specs += generated_patients(args.count, args.seed)

    session = SessionLocal()
    created = 0
    backfilled = 0
    try:
        hospital_id = resolve_hospital_id(session, args.hospital_id, args.hospital_name)
        # One query for the whole hospital rather than one per patient. At eight
        # rows the difference is invisible; at a thousand it is the difference
        # between a script that returns and one that looks hung.
        existing_by_key = {
            (p.full_name, p.date_of_birth): p
            for p in session.execute(
                select(Patient).where(Patient.hospital_id == hospital_id)
            ).scalars()
        }

        for spec in specs:
            existing = existing_by_key.get((spec["full_name"], spec["date_of_birth"]))
            if existing:
                # Backfill a genotype onto a patient seeded before the column
                # existed, so re-running this upgrades an environment instead of
                # skipping it and leaving Tier 3 with nothing to match.
                if not existing.pgx_phenotypes and spec.get("pgx_phenotypes"):
                    existing.pgx_phenotypes = list(spec["pgx_phenotypes"])
                    backfilled += 1
                continue
            session.add(
                Patient(
                    hospital_id=hospital_id,
                    full_name=spec["full_name"],
                    date_of_birth=spec["date_of_birth"],
                    blood_group=spec["blood_group"],
                    allergy_codes=list(spec["allergy_codes"]),
                    condition_codes=list(spec["condition_codes"]),
                    pgx_phenotypes=list(spec.get("pgx_phenotypes", [])),
                )
            )
            created += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    print(f"seeded {created} patient(s), backfilled {backfilled}, hospital_id={hospital_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
