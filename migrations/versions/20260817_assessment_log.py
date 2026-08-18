"""The decision trail: assessment_log.

docs/patient-profiling-usecases.md §7. Tenant class, and the table docs/services.md
§1.3's audit claim actually rests on — until now that claim had nothing behind it,
because no assessment was recorded anywhere.

Holds no patient identifier. `feature_hash` records what was asked; `actor_id`
records who asked. "Which patient was this about" is a question this table cannot
answer and the hospital's own EHR can, which is exactly the split §2.4 describes.

No RLS policy here, for consistency rather than conviction: no table in this
schema has one. `session_scope` sets `app.hospital_id` and nothing reads it, so
isolation is application-level `WHERE hospital_id` everywhere else and is
application-level here too. Adding a policy to this one table would also be a
silent no-op — the services connect as the owning role, and Postgres bypasses RLS
for table owners without FORCE ROW LEVEL SECURITY, which is worse than not having
it because it looks implemented. That gap is real and tracked separately.

Revision ID: 20260817_audit
Revises: 20260817_review
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260817_audit"
down_revision: str | None = "20260817_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assessment_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("feature_hash", sa.Text(), nullable=False),
        sa.Column("ruleset_version", sa.Text(), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assessment_log"),
    )
    # "What happened at this hospital, newest first" — the only read this table has.
    op.create_index(
        "ix_assessment_log_hospital_time", "assessment_log", ["hospital_id", "created_at"]
    )
    # Quoting a request id back at us is how a clinician disputes an answer.
    op.create_index("ix_assessment_log_request", "assessment_log", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_assessment_log_request", table_name="assessment_log")
    op.drop_index("ix_assessment_log_hospital_time", table_name="assessment_log")
    op.drop_table("assessment_log")
