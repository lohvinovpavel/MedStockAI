"""ai_audit_log: who asked the model what, and what came back.

docs/ai-module-plan.md §2/§4 Phase 2. Sibling of assessment_log
(20260817_audit) -- same actor/request_id/hospital shape, same "no RLS policy,
no REVOKE grant" reasoning: no table in this schema has one yet (services.md
§8), and the app connects as the owning role, so a policy here would be a
silent no-op that looks implemented rather than an actual one.

`hospital_id` is nullable here unlike assessment_log's: ingest's offline
prognosis CronJob calls ask_ai() with no hospital attached to the call at
all (a public FDA label, not a hospital's data) -- the same reason ai_cache
has no tenant column at all. `actor_id` is never null; those calls are
attributed to 'system:ingest'.

Revision ID: 20260818_ai_audit
Revises: 20260818_ai_cache_ver
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260818_ai_audit"
down_revision: str | None = "20260818_ai_cache_ver"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "tools_called",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_audit_log"),
    )
    op.create_index("ix_ai_audit_hospital_time", "ai_audit_log", ["hospital_id", "created_at"])
    op.create_index("ix_ai_audit_dedupe", "ai_audit_log", ["dedupe_key"])


def downgrade() -> None:
    op.drop_index("ix_ai_audit_dedupe", table_name="ai_audit_log")
    op.drop_index("ix_ai_audit_hospital_time", table_name="ai_audit_log")
    op.drop_table("ai_audit_log")
