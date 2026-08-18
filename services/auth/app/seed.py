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
from medstock_shared.demo_tenant import HOSPITAL_NAME, resolve_or_create_hospital
from medstock_shared.models import AppUser, Membership
from sqlalchemy import select

from .security import hash_password

USERS = [
    ("ann@stmarys.org", "Ann Reyes", "pharmacist"),
    ("ben@stmarys.org", "Ben Okafor", "physician"),
    ("cara@stmarys.org", "Cara Lindqvist", "director"),
    ("dan@stmarys.org", "Dan Whitfield", "admin"),
]


def main() -> None:
    password = os.environ.get("SEED_PASSWORD") or secrets.token_urlsafe(12)

    with SessionLocal() as s:
        hospital_id = resolve_or_create_hospital(s)

        created = []
        for email, full_name, role in USERS:
            if s.execute(select(AppUser).where(AppUser.email == email)).scalar_one_or_none():
                continue
            user = AppUser(email=email, password_hash=hash_password(password), full_name=full_name)
            s.add(user)
            s.flush()
            s.add(Membership(user_id=user.id, hospital_id=hospital_id, role=role))
            created.append(email)

        s.commit()

    print(f"hospital: {HOSPITAL_NAME} ({hospital_id})")
    if created:
        # Printed once and never stored. Re-running does not reprint it,
        # because it does not re-create the users.
        print(f"created: {', '.join(created)}")
        print(f"password (all four): {password}")
    else:
        print("nothing to do — all four users already exist")


if __name__ == "__main__":
    main()
