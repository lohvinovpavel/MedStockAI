"""Open enforcement: warning_letter.

docs/compliance-usecases.md §4.1. Reference class — a warning letter is about a
firm, not a hospital.

`firm_key` is the normalised name the labeler match runs on, stored rather than
derived on read for the same reasons as `import_alert.firm_key`: indexable, and
inspectable when a match is disputed.

There is no `closed` column. FDA's export publishes a Closeout Letter field and
leaves it empty on every row, so closure is not in the data; a column for it
would invite a reader to assume otherwise.

Revision ID: 20260818_wl
Revises: 20260817_comp
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_wl"
down_revision: str | None = "20260817_comp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "warning_letter",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("firm_key", sa.Text(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("posted_date", sa.Date(), nullable=True),
        sa.Column("issuing_office", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("has_response", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_warning_letter"),
        # One firm can receive several letters; the subject is what separates
        # them when two land on the same day.
        sa.UniqueConstraint("company_name", "issue_date", "subject", name="uq_warning_letter"),
    )
    op.create_index("ix_warning_letter_firm_key", "warning_letter", ["firm_key"])


def downgrade() -> None:
    op.drop_index("ix_warning_letter_firm_key", table_name="warning_letter")
    op.drop_table("warning_letter")
