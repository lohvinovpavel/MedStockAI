"""Warehouse (issue #8): facility/location registry, consumption history,
condition telemetry, drug storage requirements.

Revision ID: 20260817_warehouse
Revises: 20260815_patient
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260817_warehouse"
down_revision: str | None = "20260815_patient"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "facility",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("lon", sa.Numeric(9, 6), nullable=True),
        sa.Column("operated", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospital.id"]),
        sa.CheckConstraint(
            "type IN ('Hospital','Clinic','Pharmacy','Warehouse')", name="ck_facility_type"
        ),
        sa.UniqueConstraint("hospital_id", "code", name="uq_facility_hospital_code"),
    )
    op.create_table(
        "storage_location",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("facility_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["facility_id"], ["facility.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "kind IN ('room','fridge','freezer','cold_room')", name="ck_storage_location_kind"
        ),
        sa.UniqueConstraint("facility_id", "code", name="uq_storage_location_facility_code"),
    )
    op.create_table(
        "consumption_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", sa.Text(), nullable=False),
        sa.Column("facility_id", sa.BigInteger(), nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("rxcui", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("qty_consumed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stockout", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["facility_id"], ["facility.id"]),
        sa.UniqueConstraint(
            "hospital_id", "facility_id", "ndc", "date", name="uq_consumption_daily_natural"
        ),
    )
    op.create_index(
        "ix_consumption_daily_series", "consumption_daily", ["facility_id", "ndc", "date"]
    )
    op.create_table(
        "location_condition",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("location_id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temperature_c", sa.Numeric(5, 2), nullable=False),
        sa.Column("humidity_pct", sa.Numeric(5, 2), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["location_id"], ["storage_location.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("location_id", "ts", name="uq_location_condition_natural"),
    )
    # B1: facility_id carries site identity from now on; location_id stays as
    # the intra-facility shelf code. Nullable — pre-B1 rows are backfilled by
    # the demo seeder, not here (facility rows don't exist yet at migrate time).
    op.add_column(
        "stock_snapshot", sa.Column("facility_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_stock_snapshot_facility", "stock_snapshot", "facility", ["facility_id"], ["id"]
    )
    # Location codes repeat across facilities (every clinic has a "fridge-1"),
    # so facility joins the natural key. NULLS NOT DISTINCT keeps pre-B1 rows
    # (facility_id IS NULL) under the old uniqueness rule.
    op.drop_constraint("uq_stock_hospital_ndc_loc", "stock_snapshot", type_="unique")
    op.create_unique_constraint(
        "uq_stock_hospital_ndc_fac_loc",
        "stock_snapshot",
        ["hospital_id", "ndc", "facility_id", "location_id"],
        postgresql_nulls_not_distinct=True,
    )
    # Class-level storage requirements on the global drug reference table —
    # what the warehouse excursion check joins telemetry against.
    op.add_column("drug", sa.Column("storage_class", sa.Text(), nullable=True))
    op.add_column("drug", sa.Column("storage_min_c", sa.Numeric(5, 2), nullable=True))
    op.add_column("drug", sa.Column("storage_max_c", sa.Numeric(5, 2), nullable=True))
    op.add_column("drug", sa.Column("humidity_max_pct", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("drug", "humidity_max_pct")
    op.drop_column("drug", "storage_max_c")
    op.drop_column("drug", "storage_min_c")
    op.drop_column("drug", "storage_class")
    op.drop_constraint("uq_stock_hospital_ndc_fac_loc", "stock_snapshot", type_="unique")
    # Facility-scoped rows exist only under this revision's model and would
    # collide under the old (hospital_id, ndc, location_id) key — every
    # facility has a "fridge-1". Destructive by necessity.
    op.execute("DELETE FROM stock_snapshot WHERE facility_id IS NOT NULL")
    op.create_unique_constraint(
        "uq_stock_hospital_ndc_loc", "stock_snapshot", ["hospital_id", "ndc", "location_id"]
    )
    op.drop_constraint("fk_stock_snapshot_facility", "stock_snapshot", type_="foreignkey")
    op.drop_column("stock_snapshot", "facility_id")
    op.drop_table("location_condition")
    op.drop_index("ix_consumption_daily_series", table_name="consumption_daily")
    op.drop_table("consumption_daily")
    op.drop_table("storage_location")
    op.drop_table("facility")
