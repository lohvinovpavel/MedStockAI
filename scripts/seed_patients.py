"""Seed demo patients for the physician prescribe cart.

Demo PHI exception — stores name/DOB in `patient` for the capstone UI.
Maps to PatientVector only at /cart-check time.

  uv run --no-sync python scripts/seed_patients.py --hospital-id <uuid>
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "shared"))

from medstock_shared.db import SessionLocal
from medstock_shared.models import Patient
from sqlalchemy import select

DEFAULT_HOSPITAL_ID = "00000000-0000-0000-0000-000000000001"

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo patient profiles")
    parser.add_argument("--hospital-id", default=DEFAULT_HOSPITAL_ID)
    args = parser.parse_args()
    hospital_id = args.hospital_id.strip()

    session = SessionLocal()
    created = 0
    backfilled = 0
    try:
        for spec in DEMO_PATIENTS:
            existing = session.execute(
                select(Patient).where(
                    Patient.hospital_id == hospital_id,
                    Patient.full_name == spec["full_name"],
                    Patient.date_of_birth == spec["date_of_birth"],
                )
            ).scalar_one_or_none()
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
