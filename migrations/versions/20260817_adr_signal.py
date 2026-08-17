"""Tier 1 population signal: adr_signal.

docs/patient-profiling-usecases.md §3 Tier 1, and stage 7 of
docs/patient-pipeline.md §2 ("Population signal — precomputed FAERS PRR/ROR for
this drug × serious reaction"). Reference class: a reporting ratio is about a
drug, not a person.

Written offline by services/ingest/app/faers.py. Nothing here is computed on a
request: the whole point of precomputing is that stage 7 is a lookup.

Revision ID: 20260817_adr
Revises: 20260817_pgx
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_adr"
down_revision: str | None = "20260817_pgx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "adr_signal",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("rxcui", sa.Text(), nullable=False),
        sa.Column("reaction", sa.Text(), nullable=False),
        sa.Column("prr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ror", sa.Float(), nullable=False, server_default="0"),
        # Kept alongside the ratios because a ratio over 3 reports is noise, and
        # the standard signal criteria require a minimum count for that reason.
        sa.Column("n_reports", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_drug_reports", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_adr_signal"),
        sa.UniqueConstraint("rxcui", "reaction", name="uq_adr_signal_natural"),
    )
    op.create_index("ix_adr_signal_rxcui", "adr_signal", ["rxcui"])


def downgrade() -> None:
    op.drop_index("ix_adr_signal_rxcui", table_name="adr_signal")
    op.drop_table("adr_signal")
