"""Demo patient table for physician prescription cart (PHI exception).

Tenant class — hospital_id Text like formulary_item / stock_snapshot.
Revision ID: 20260815_patient
Revises: 20260814_cert
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260815_patient"
down_revision: str | None = "20260814_cert"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patient",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("blood_group", sa.Text(), nullable=True),
        sa.Column(
            "allergy_codes",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "condition_codes",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_patient_hospital_id", "patient", ["hospital_id"])


def downgrade() -> None:
    op.drop_index("ix_patient_hospital_id", table_name="patient")
    op.drop_table("patient")
