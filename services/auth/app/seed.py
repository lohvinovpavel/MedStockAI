"""python -m app.seed — one hospital, four users, one per role.

Idempotent: running it twice against a live database is safe and changes
nothing. Not an Alembic data migration (a migration cannot print a generated
password and cannot be re-run selectively) and not a test fixture (the demo
needs these rows in the real database).

Run it by hand. Wiring it into migrate-job would put demo accounts into every
environment that ever runs a migration.
"""

import os
import secrets

from medstock_shared.db import SessionLocal
from medstock_shared.models import AppUser, Hospital, Membership
from sqlalchemy import select

from .security import hash_password

DEMO_HOSPITAL_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
HOSPITAL_NAME = "St Mary's General"
USERS = [
    ("ann@stmarys.org", "Ann Reyes", "pharmacist"),
    ("ben@stmarys.org", "Ben Okafor", "physician"),
    ("cara@stmarys.org", "Cara Lindqvist", "director"),
    ("dan@stmarys.org", "Dan Whitfield", "admin"),
]


def main() -> None:
    password = os.environ.get("SEED_PASSWORD") or secrets.token_urlsafe(12)

    with SessionLocal() as s:
        hospital = s.execute(
            select(Hospital).where(
                (Hospital.id == DEMO_HOSPITAL_ID) | (Hospital.name == HOSPITAL_NAME)
            )
        ).scalar_one_or_none()
        if hospital is None:
            hospital = Hospital(id=DEMO_HOSPITAL_ID, name=HOSPITAL_NAME)
            s.add(hospital)
            s.flush()

        created = []
        for email, full_name, role in USERS:
            if s.execute(select(AppUser).where(AppUser.email == email)).scalar_one_or_none():
                continue
            user = AppUser(email=email, password_hash=hash_password(password), full_name=full_name)
            s.add(user)
            s.flush()
            s.add(Membership(user_id=user.id, hospital_id=hospital.id, role=role))
            created.append(email)

        s.commit()

    print(f"hospital: {HOSPITAL_NAME} ({hospital.id})")
    if created:
        # Printed once and never stored. Re-running does not reprint it,
        # because it does not re-create the users.
        print(f"created: {', '.join(created)}")
        print(f"password (all four): {password}")
    else:
        print("nothing to do — all four users already exist")


if __name__ == "__main__":
    main()
