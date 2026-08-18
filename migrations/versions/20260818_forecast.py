"""Prediction (issue #7, spec E1): forecast_point — quantile demand forecasts
written per run, read by GET /forecast and /at-risk.

Deviations from the E1 DDL, both deliberate:
- hospital_id is Text, not uuid — it must join stock_snapshot and
  consumption_daily, and both already carry Text (20260817_warehouse).
- data_through (date) is added per row: the last consumption date the run
  saw. Constant within a run; it is what lets a client detect that data has
  outrun the forecast and trigger a re-run without a second query.

Revision ID: 20260818_forecast
Revises: 20260817_comp
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260818_forecast"
down_revision: str | None = "20260817_comp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forecast_point",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", sa.Text(), nullable=False),
        sa.Column("facility_id", sa.BigInteger(), nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("run_id", UUID(as_uuid=False), nullable=False),
        sa.Column("data_through", sa.Date(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("p10", sa.Numeric(), nullable=False),
        sa.Column("p50", sa.Numeric(), nullable=False),
        sa.Column("p90", sa.Numeric(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["facility_id"], ["facility.id"]),
        sa.CheckConstraint("p10 <= p50 AND p50 <= p90", name="ck_forecast_point_quantiles"),
        sa.UniqueConstraint(
            "hospital_id",
            "facility_id",
            "ndc",
            "run_id",
            "target_date",
            name="uq_forecast_point_natural",
        ),
    )
    op.create_index(
        "ix_forecast_lookup",
        "forecast_point",
        ["hospital_id", "facility_id", "ndc", "run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_forecast_lookup", table_name="forecast_point")
    op.drop_table("forecast_point")
