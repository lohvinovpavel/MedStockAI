"""stock_daily — end-of-day on-hand per facility/NDC (issue #7 follow-up).

The per-day stock history the forecasts page draws left of "today". Nothing
records this in production yet — B4 receiving/consume events are the future
writer; until then the demo seeder plants a series that is consistent with
consumption_daily and ends exactly at stock_snapshot's current quantity.

Revision ID: 20260818_stock_daily
Revises: 20260818_forecast
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_stock_daily"
down_revision: str | None = "20260818_forecast"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", sa.Text(), nullable=False),
        sa.Column("facility_id", sa.BigInteger(), nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("qty_on_hand", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["facility_id"], ["facility.id"]),
        sa.UniqueConstraint(
            "hospital_id", "facility_id", "ndc", "date", name="uq_stock_daily_natural"
        ),
    )
    op.create_index("ix_stock_daily_series", "stock_daily", ["facility_id", "ndc", "date"])


def downgrade() -> None:
    op.drop_index("ix_stock_daily_series", table_name="stock_daily")
    op.drop_table("stock_daily")
