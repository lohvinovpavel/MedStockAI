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

DEMO_PATIENTS = (
    {
        "full_name": "Elena Vasquez",
        "date_of_birth": date(1978, 4, 12),
        "blood_group": "A+",
        "allergy_codes": [],
        "condition_codes": ["avoid_caffeine"],
    },
    {
        "full_name": "Marcus Chen",
        "date_of_birth": date(1990, 11, 3),
        "blood_group": "O-",
        "allergy_codes": ["penicillin"],
        "condition_codes": [],
    },
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo patient profiles")
    parser.add_argument("--hospital-id", default=DEFAULT_HOSPITAL_ID)
    args = parser.parse_args()
    hospital_id = args.hospital_id.strip()

    session = SessionLocal()
    created = 0
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
                continue
            session.add(
                Patient(
                    hospital_id=hospital_id,
                    full_name=spec["full_name"],
                    date_of_birth=spec["date_of_birth"],
                    blood_group=spec["blood_group"],
                    allergy_codes=list(spec["allergy_codes"]),
                    condition_codes=list(spec["condition_codes"]),
                )
            )
            created += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    print(f"seeded {created} patient(s) for hospital_id={hospital_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
