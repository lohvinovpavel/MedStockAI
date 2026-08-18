"""Tier 3 pharmacogenomics: pgx_guideline, plus phenotypes on the demo patient.

docs/patient-profiling-usecases.md §3, Tier 3 — the CPIC backbone. Reference
class: a guideline is about a drug and a phenotype, never about a person.

The `patient.pgx_phenotypes` column is the demo half. Tier 3 is a lookup keyed
on genotype, and nothing in this system supplied genotype before now, so without
it the tier would be built, correct and permanently invisible — which is exactly
how PP-3 shipped and sat unused until the approval gate was added.

Phenotypes, not diplotypes: mapping *2/*2 onto "Poor Metabolizer" needs CPIC's
allele-definition tables, and getting it wrong is a clinical error we would have
authored. The reporting lab already states the phenotype and every CPIC
recommendation is keyed on it.

Revision ID: 20260817_pgx
Revises: 20260817_audit
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260817_pgx"
down_revision: str | None = "20260817_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pgx_guideline",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("gene", sa.Text(), nullable=False),
        sa.Column("rxcui", sa.Text(), nullable=False),
        # CPIC's own lookupkey value, not a vocabulary of ours.
        sa.Column("phenotype", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False, server_default=""),
        sa.Column("implication", sa.Text(), nullable=False, server_default=""),
        sa.Column("classification", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_level", sa.Text(), nullable=False, server_default=""),
        # CPIC's three prescribing booleans OR-ed: does this genotype change
        # anything, or is the answer "standard dosing"?
        sa.Column("action_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("population", sa.Text(), nullable=False, server_default="general"),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pgx_guideline"),
        sa.UniqueConstraint("gene", "rxcui", "phenotype", "population", name="uq_pgx_guideline"),
    )
    # The request path looks these up by drug.
    op.create_index("ix_pgx_guideline_rxcui", "pgx_guideline", ["rxcui"])

    op.add_column(
        "patient",
        sa.Column(
            "pgx_phenotypes",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("patient", "pgx_phenotypes")
    op.drop_index("ix_pgx_guideline_rxcui", table_name="pgx_guideline")
    op.drop_table("pgx_guideline")
