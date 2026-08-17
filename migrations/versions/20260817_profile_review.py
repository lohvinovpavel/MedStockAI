"""PP-5 approval gate: make drug_risk_profile reviewable.

The table already had `status` and `approved_by`, and nothing in the system
could write either. Every profile the extraction job produces sits at
`awaiting_approval` for ever, and `approved_profiles()` filters to `approved` —
so PP-3 and PP-4 read an empty table on any real deployment
(docs/prognosis-and-procurement.md §1.3, gate 3).

Three changes, all in service of a review a pharmacist can actually perform:

* `approved_by` -> `reviewed_by`. A rejection has a reviewer too, and recording
  who rejected a profile under a column named `approved_by` is the kind of
  thing that reads as an approval a year later. Safe to rename: no code reads
  the column today, which is the whole problem this revision exists to fix.
* `reviewed_at`, `review_note`. `extracted_at` cannot double as the review
  timestamp — it carries `onupdate=now()`, so re-extraction moves it. The note
  is where a rejection says why, which is what stops the same bad extraction
  being re-reviewed from scratch every cycle.
* A CHECK on `status`. A typo'd status fails safe (it is not 'approved', so it
  is never served) and is therefore invisible. The constraint makes it loud.

Revision ID: 20260817_review
Revises: 20260817_assume
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_review"
down_revision: str | None = "20260817_assume"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = ("awaiting_approval", "approved", "rejected")


def upgrade() -> None:
    op.alter_column("drug_risk_profile", "approved_by", new_column_name="reviewed_by")
    op.add_column(
        "drug_risk_profile",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "drug_risk_profile",
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
    )

    # Any row written before this revision was written by the extraction job and
    # never reviewed, so this only ever normalises a value nobody set.
    op.execute(
        sa.text(
            "UPDATE drug_risk_profile SET status = 'awaiting_approval' "
            "WHERE status NOT IN :allowed"
        ).bindparams(sa.bindparam("allowed", value=_STATUSES, expanding=True))
    )
    op.create_check_constraint(
        "ck_drug_risk_profile_status",
        "drug_risk_profile",
        sa.column("status").in_(_STATUSES),
    )

    # The review queue is "everything awaiting approval, oldest first", which is
    # a scan of the whole table without this.
    op.create_index(
        "ix_drug_risk_profile_status", "drug_risk_profile", ["status", "extracted_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_drug_risk_profile_status", table_name="drug_risk_profile")
    op.drop_constraint("ck_drug_risk_profile_status", "drug_risk_profile", type_="check")
    op.drop_column("drug_risk_profile", "review_note")
    op.drop_column("drug_risk_profile", "reviewed_at")
    op.alter_column("drug_risk_profile", "reviewed_by", new_column_name="approved_by")
