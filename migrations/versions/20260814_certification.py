"""COMP-1 certification traffic light: drug_certification + certification_finding.

Reference class (services.md §1.1) — global, no hospital_id, no RLS. FDA
certification is identical for every hospital, so it is polled once for all of
them by services/ingest/app/certification.py.

Revision ID: 20260814_cert
Revises: 20260813_uc1
Create Date: 2026-08-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260814_cert"
down_revision: Union[str, None] = "20260813_uc1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drug_certification",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("marketing_end_date", sa.Date(), nullable=True),
        sa.Column("listing_expiration_date", sa.Date(), nullable=True),
        sa.Column("marketing_category", sa.Text(), nullable=True),
        sa.Column("application_number", sa.Text(), nullable=True),
        sa.Column("labeler", sa.Text(), nullable=True),
        sa.Column("provenance", sa.Text(), nullable=False, server_default="scheduled"),
        sa.Column("ruleset_version", sa.Text(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "raw", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ndc", name="uq_drug_certification_ndc"),
    )
    op.create_table(
        "certification_finding",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "raw", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ndc", "code", "source_ref", name="uq_cert_finding_natural"),
    )
    # /status looks findings up by NDC for a whole page of stock at once.
    op.create_index("ix_certification_finding_ndc", "certification_finding", ["ndc"])


def downgrade() -> None:
    op.drop_index("ix_certification_finding_ndc", table_name="certification_finding")
    op.drop_table("certification_finding")
    op.drop_table("drug_certification")
