"""PP-3 prognosis: drug_risk_profile.

Reference class (services.md §1.1) — the profile describes a drug, not a person.
Written by services/ingest/app/prognosis.py, read by patient-profiling.

Revision ID: 20260814_prog
Revises: 20260815_patient
Create Date: 2026-08-14

Re-pointed from 20260814_cert onto 20260815_patient after rebasing on main.
Both were written against cert as their parent, which left alembic with two
heads and `upgrade head` refusing to pick one. This branch is the one that has
not merged yet, so it moves; the revision id is left alone because it is already
stamped in dev databases.

The id still reads 08-14 while it now follows an 08-15 revision. Alembic orders
by the down_revision chain, not by the name, so this is cosmetic -- and renaming
a stamped revision to fix cosmetics would be a worse trade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260814_prog"
down_revision: str | None = "20260815_patient"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "drug_risk_profile",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("rxcui", sa.Text(), nullable=False),
        sa.Column("reaction", sa.Text(), nullable=False),
        sa.Column("seriousness", sa.Text(), nullable=False, server_default="moderate"),
        sa.Column(
            "risk_factors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("citation", sa.Text(), nullable=False, server_default=""),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("spl_id", sa.Text(), nullable=True),
        # Nothing colours a screen until a pharmacist moves this to 'approved'.
        sa.Column("status", sa.Text(), nullable=False, server_default="awaiting_approval"),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rxcui", "reaction", name="uq_drug_risk_profile_natural"),
    )
    # The request path looks these up by drug, filtered to approved.
    op.create_index("ix_drug_risk_profile_rxcui", "drug_risk_profile", ["rxcui"])


def downgrade() -> None:
    op.drop_index("ix_drug_risk_profile_rxcui", table_name="drug_risk_profile")
    op.drop_table("drug_risk_profile")
