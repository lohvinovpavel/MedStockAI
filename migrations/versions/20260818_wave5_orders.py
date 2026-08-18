"""Wave 5: F3 purchase orders, G2 transfers, sequences, RLS, H1 triggers.

Revision ID: 20260818_wave5
Revises: 20260818_wave4
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260818_wave5"
down_revision: str | None = "20260818_wave4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT = """
CREATE POLICY tenant_isolation ON {table}
  USING      (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)
  WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)
"""

_LINE_POLICY = """
CREATE POLICY tenant_isolation ON purchase_order_line
  USING (EXISTS (
    SELECT 1 FROM purchase_order po
    WHERE po.id = purchase_order_line.purchase_order_id
      AND po.hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM purchase_order po
    WHERE po.id = purchase_order_line.purchase_order_id
      AND po.hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid
  ))
"""


def _rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(_TENANT.format(table=table)))


def _audit(table: str) -> None:
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER audit_{table}
              AFTER INSERT OR UPDATE ON {table}
              FOR EACH ROW EXECUTE FUNCTION write_audit_entry();
            """
        )
    )


def upgrade() -> None:
    op.execute(sa.text("CREATE SEQUENCE purchase_order_ref_seq START WITH 149"))
    op.execute(sa.text("CREATE SEQUENCE transfer_request_ref_seq START WITH 31"))

    op.create_table(
        "purchase_order",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("hospital_id", UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", sa.BigInteger(), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("review_decision_id", sa.BigInteger(), nullable=True),
        sa.Column("shipping", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_delivery", sa.Date(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft','placed','in_transit','delivered','cancelled')",
            name="ck_purchase_order_status",
        ),
        sa.CheckConstraint(
            "source IN ('ai_suggestion','manual')",
            name="ck_purchase_order_source",
        ),
        sa.CheckConstraint(
            "source = 'manual' OR review_decision_id IS NOT NULL",
            name="ck_purchase_order_ai_has_decision",
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospital.id"], name="fk_purchase_order_hospital"),
        sa.ForeignKeyConstraint(["facility_id"], ["facility.id"], name="fk_purchase_order_facility"),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"], name="fk_purchase_order_supplier"),
        sa.ForeignKeyConstraint(
            ["review_decision_id"],
            ["review_decision.id"],
            name="fk_purchase_order_review_decision",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_order"),
        sa.UniqueConstraint("hospital_id", "ref", name="uq_purchase_order_hospital_ref"),
    )
    op.create_index(
        "ix_purchase_order_status_created",
        "purchase_order",
        ["hospital_id", "status", "created_at"],
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uq_purchase_order_idempotency
              ON purchase_order (hospital_id, idempotency_key)
              WHERE idempotency_key IS NOT NULL
            """
        )
    )

    op.create_table(
        "purchase_order_line",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("purchase_order_id", sa.BigInteger(), nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 4), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_purchase_order_line_qty"),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_order.id"],
            name="fk_purchase_order_line_order",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_order_line"),
        sa.UniqueConstraint(
            "purchase_order_id", "ndc", name="uq_purchase_order_line_ndc"
        ),
    )

    op.create_table(
        "transfer_request",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("hospital_id", UUID(as_uuid=True), nullable=False),
        sa.Column("from_facility_id", sa.BigInteger(), nullable=False),
        sa.Column("to_facility_id", sa.BigInteger(), nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="requested"),
        sa.Column("shortage_id", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reserved_lots", JSONB(), nullable=False, server_default="[]"),
        sa.Column("requested_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_transfer_request_qty"),
        sa.CheckConstraint(
            "status IN ('requested','dispatched','received','cancelled')",
            name="ck_transfer_request_status",
        ),
        sa.CheckConstraint(
            "from_facility_id <> to_facility_id",
            name="ck_transfer_request_distinct_facilities",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id"], ["hospital.id"], name="fk_transfer_request_hospital"
        ),
        sa.ForeignKeyConstraint(
            ["from_facility_id"], ["facility.id"], name="fk_transfer_request_from"
        ),
        sa.ForeignKeyConstraint(
            ["to_facility_id"], ["facility.id"], name="fk_transfer_request_to"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transfer_request"),
        sa.UniqueConstraint("hospital_id", "ref", name="uq_transfer_request_hospital_ref"),
    )

    _rls("purchase_order")
    _rls("transfer_request")
    op.execute(sa.text("ALTER TABLE purchase_order_line ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE purchase_order_line FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(_LINE_POLICY))
    _audit("purchase_order")
    _audit("transfer_request")

    op.execute(sa.text("GRANT USAGE, SELECT ON SEQUENCE purchase_order_ref_seq TO app_role"))
    op.execute(sa.text("GRANT USAGE, SELECT ON SEQUENCE transfer_request_ref_seq TO app_role"))


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_transfer_request ON transfer_request"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_purchase_order ON purchase_order"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON purchase_order_line"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON transfer_request"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON purchase_order"))
    op.drop_table("transfer_request")
    op.drop_table("purchase_order_line")
    op.drop_index("uq_purchase_order_idempotency", table_name="purchase_order")
    op.drop_index("ix_purchase_order_status_created", table_name="purchase_order")
    op.drop_table("purchase_order")
    op.execute(sa.text("DROP SEQUENCE IF EXISTS transfer_request_ref_seq"))
    op.execute(sa.text("DROP SEQUENCE IF EXISTS purchase_order_ref_seq"))
