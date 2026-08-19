"""What the extraction did, for the pharmacist who approves it.

The PP-3 gate asks a pharmacist to accept or reject a risk profile, but the row
they see is only what survived: factors that could not be expressed in the
collected vocabulary were dropped before it, silently, and a profile that lost a
condition matches MORE patients than the label describes. Approving that is not
a review, because the thing worth objecting to is not on the screen.

This column carries the account -- kept, repaired, dropped, and any label section
that failed to read. Written by the graph path only (`--graph`); the single-shot
path leaves it empty, which is why it defaults to '' rather than being NOT NULL
with no default.

Revision ID: 20260818_note
Revises: 20260818_wave6
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_note"
down_revision: str | None = "20260818_wave6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("drug_risk_profile")]
    if "extraction_note" not in columns:
        op.add_column(
            "drug_risk_profile",
            sa.Column("extraction_note", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("drug_risk_profile")]
    if "extraction_note" in columns:
        op.drop_column("drug_risk_profile", "extraction_note")
