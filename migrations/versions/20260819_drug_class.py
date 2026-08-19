"""Add drug.drug_class — primary therapeutic class (RxClass: ATC preferred,
VA fallback), one class per drug, populated by the ingest backfill / demo seed.

Revision ID: 20260819_drug_class
Revises: 20260818_wave6
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_drug_class"
down_revision: str | None = "20260818_wave6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("drug", sa.Column("drug_class", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("drug", "drug_class")
