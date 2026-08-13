"""UC-1 tenant tables: formulary boost + stock-by-rxcui.

Revision ID: 20260813_uc1
Revises:
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_uc1"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "formulary_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", sa.Text(), nullable=False),
        sa.Column("rxcui", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hospital_id", "rxcui", name="uq_formulary_hospital_rxcui"),
    )
    op.create_table(
        "stock_snapshot",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", sa.Text(), nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hospital_id", "ndc", "location_id", name="uq_stock_hospital_ndc_loc"),
    )


def downgrade() -> None:
    op.drop_table("stock_snapshot")
    op.drop_table("formulary_item")
