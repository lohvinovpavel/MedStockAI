"""Wave 6: I2 copilot conversation persistence (FORCE RLS).

H2 provenance is a code change on ask_ai / AITask (dedupe_key includes
prompt_version + model; SET LOCAL app.ai_dedupe_key). ai_cache already has
those columns from 20260818_ai_cache_versioning.

Revision ID: 20260818_wave6
Revises: 20260818_wave5
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260818_wave6"
down_revision: str | None = "20260818_wave5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT = """
CREATE POLICY tenant_isolation ON {table}
  USING      (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)
  WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)
"""


def _rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(_TENANT.format(table=table)))


def upgrade() -> None:
    op.create_table(
        "copilot_conversation",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["hospital_id"], ["hospital.id"], name="fk_copilot_conversation_hospital"
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"], ["facility.id"], name="fk_copilot_conversation_facility"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_copilot_conversation"),
    )
    op.create_table(
        "copilot_message",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("card", JSONB(), nullable=True),
        sa.Column("tool_name", sa.Text(), nullable=True),
        sa.Column("ai_dedupe_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user','assistant','tool')", name="ck_copilot_message_role"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["copilot_conversation.id"],
            name="fk_copilot_message_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id"], ["hospital.id"], name="fk_copilot_message_hospital"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_copilot_message"),
    )
    op.create_index(
        "ix_copilot_msg", "copilot_message", ["conversation_id", "created_at"]
    )
    _rls("copilot_conversation")
    _rls("copilot_message")


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON copilot_message"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON copilot_conversation"))
    op.drop_index("ix_copilot_msg", table_name="copilot_message")
    op.drop_table("copilot_message")
    op.drop_table("copilot_conversation")
