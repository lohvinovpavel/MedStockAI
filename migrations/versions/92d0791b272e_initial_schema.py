"""initial schema

Revision ID: 92d0791b272e
Revises:
Create Date: 2026-08-13 16:24:00.449505
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '92d0791b272e'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # AppUser.email is CITEXT. The extension has to exist before the table
    # that uses it. Available in the postgres:16 image and on Cloud SQL.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.create_table(
        "ai_cache",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type", "dedupe_key", name="uq_ai_cache_type_dedupe"),
    )

    op.create_table(
        "drug",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ndc"),
    )

    op.create_table(
        "shortage_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("ndc", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
    )

    op.create_table(
        "drug_price",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("effective_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unit_price", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ndc", "effective_date", name="uq_drug_price_ndc_date"),
    )

    op.create_table(
        "rxnorm_edge",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("rxcui_from", sa.Text(), nullable=False),
        sa.Column("rxcui_to", sa.Text(), nullable=False),
        sa.Column("relationship", sa.Text(), nullable=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rxcui_from", "rxcui_to", "relationship", name="uq_rxnorm_edge"
        ),
    )

    op.create_table(
        "hospital",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "app_user",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "membership",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospital.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "hospital_id"),
        sa.CheckConstraint(
            "role IN ('pharmacist','physician','director','admin')",
            name="ck_membership_role",
        ),
        sa.UniqueConstraint("user_id", name="uq_membership_one_hospital_per_user"),
    )


def downgrade() -> None:
    op.drop_table("membership")
    op.drop_table("app_user")
    op.drop_table("hospital")
    op.drop_table("rxnorm_edge")
    op.drop_table("drug_price")
    op.drop_table("shortage_event")
    op.drop_table("drug")
    op.drop_table("ai_cache")

    op.execute("DROP EXTENSION IF EXISTS citext")
