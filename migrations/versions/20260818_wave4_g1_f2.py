"""Wave 4: F2 supplier + catalog (tenant, FORCE RLS) for warehouse quotes.

G1 shortage matrix reads existing stock_snapshot / shortage_event / E2
trailing consumption — no new table.

Revision ID: 20260818_wave4
Revises: 20260818_wave3
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260818_wave4"
down_revision: str | None = "20260818_wave3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supplier",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("reliability_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("shipping_flat", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.CheckConstraint("lead_time_days >= 0", name="ck_supplier_lead_nonneg"),
        sa.CheckConstraint(
            "reliability_pct >= 0 AND reliability_pct <= 100",
            name="ck_supplier_reliability_pct",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id"], ["hospital.id"], name="fk_supplier_hospital"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_supplier"),
        sa.UniqueConstraint("hospital_id", "name", name="uq_supplier_hospital_name"),
    )
    op.create_table(
        "supplier_catalog",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("pack_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("min_order_qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("unit_cost >= 0", name="ck_supplier_catalog_cost_nonneg"),
        sa.CheckConstraint("pack_size >= 1", name="ck_supplier_catalog_pack"),
        sa.CheckConstraint("min_order_qty >= 1", name="ck_supplier_catalog_min_order"),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["supplier.id"],
            name="fk_supplier_catalog_supplier",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_supplier_catalog"),
        sa.UniqueConstraint(
            "supplier_id", "ndc", name="uq_supplier_catalog_supplier_ndc"
        ),
    )

    op.execute(sa.text("ALTER TABLE supplier ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE supplier FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON supplier
              USING      (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)
              WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)
            """
        )
    )
    op.execute(sa.text("ALTER TABLE supplier_catalog ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE supplier_catalog FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON supplier_catalog
              USING (EXISTS (
                SELECT 1 FROM supplier s
                WHERE s.id = supplier_catalog.supplier_id
                  AND s.hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid
              ))
              WITH CHECK (EXISTS (
                SELECT 1 FROM supplier s
                WHERE s.id = supplier_catalog.supplier_id
                  AND s.hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid
              ))
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON supplier_catalog"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON supplier"))
    op.execute(sa.text("ALTER TABLE supplier_catalog DISABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE supplier DISABLE ROW LEVEL SECURITY"))
    op.drop_table("supplier_catalog")
    op.drop_table("supplier")
