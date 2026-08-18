"""Wave 2: stock_batch + par_level, rollup trigger, H1 audit on those
tables, FORCE RLS on the remaining tenant tables, grants for app_role.

Revision ID: 20260818_wave2
Revises: 20260818_h1_audit
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260818_wave2"
down_revision: str | None = "20260818_h1_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HOSPITAL_ID_TABLES = (
    "formulary_item",
    "stock_snapshot",
    "facility",
    "consumption_daily",
    "forecast_point",
    "stock_daily",
    "patient",
    "assessment_log",
    "stock_batch",
    "par_level",
)


def upgrade() -> None:
    op.create_table(
        "stock_batch",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", sa.BigInteger(), nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("lot", sa.Text(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_stock_batch_qty"),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospital.id"], name="fk_stock_batch_hospital"),
        sa.ForeignKeyConstraint(["facility_id"], ["facility.id"], name="fk_stock_batch_facility"),
        sa.PrimaryKeyConstraint("id", name="pk_stock_batch"),
        sa.UniqueConstraint(
            "hospital_id", "facility_id", "ndc", "lot", name="uq_stock_batch_natural"
        ),
    )
    op.execute(
        sa.text("CREATE INDEX ix_stock_batch_fefo ON stock_batch (hospital_id, ndc, expiry_date)")
    )

    op.create_table(
        "par_level",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", sa.BigInteger(), nullable=False),
        sa.Column("ndc", sa.Text(), nullable=False),
        sa.Column("reorder_point", sa.Integer(), nullable=False),
        sa.Column("target_qty", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("reorder_point >= 0", name="ck_par_reorder_nonneg"),
        sa.CheckConstraint("target_qty > reorder_point", name="ck_par_target_above_reorder"),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospital.id"], name="fk_par_level_hospital"),
        sa.ForeignKeyConstraint(["facility_id"], ["facility.id"], name="fk_par_level_facility"),
        sa.PrimaryKeyConstraint("id", name="pk_par_level"),
        sa.UniqueConstraint(
            "hospital_id", "facility_id", "ndc", name="uq_par_level_natural"
        ),
    )

    op.execute(
        sa.text(
            """
            CREATE FUNCTION refresh_stock_snapshot(hid uuid, fid bigint, n text, loc text)
            RETURNS void AS $BODY$
            DECLARE
              qty int;
            BEGIN
              SELECT COALESCE(SUM(quantity), 0) INTO qty
              FROM stock_batch
              WHERE hospital_id = hid AND facility_id = fid AND ndc = n AND location_id = loc;
              INSERT INTO stock_snapshot (hospital_id, facility_id, ndc, location_id, quantity)
              VALUES (hid, fid, n, loc, qty)
              ON CONFLICT ON CONSTRAINT uq_stock_hospital_ndc_fac_loc
              DO UPDATE SET quantity = EXCLUDED.quantity, updated_at = now();
            END;
            $BODY$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION stock_batch_rollup() RETURNS trigger AS $BODY$
            BEGIN
              IF TG_OP = 'UPDATE'
                 AND (OLD.hospital_id, OLD.facility_id, OLD.ndc, OLD.location_id)
                  IS DISTINCT FROM
                     (NEW.hospital_id, NEW.facility_id, NEW.ndc, NEW.location_id)
              THEN
                PERFORM refresh_stock_snapshot(
                  OLD.hospital_id, OLD.facility_id, OLD.ndc, OLD.location_id
                );
              END IF;
              IF TG_OP = 'DELETE' THEN
                PERFORM refresh_stock_snapshot(
                  OLD.hospital_id, OLD.facility_id, OLD.ndc, OLD.location_id
                );
                RETURN OLD;
              END IF;
              PERFORM refresh_stock_snapshot(
                NEW.hospital_id, NEW.facility_id, NEW.ndc, NEW.location_id
              );
              RETURN NEW;
            END;
            $BODY$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER stock_batch_rollup
              AFTER INSERT OR UPDATE OR DELETE ON stock_batch
              FOR EACH ROW EXECUTE FUNCTION stock_batch_rollup();
            """
        )
    )

    # Existing shelf rows become one seed lot each. Audit trigger is attached
    # after this so the backfill does not need an actor.
    op.execute(
        sa.text(
            """
            INSERT INTO stock_batch (
              hospital_id, facility_id, ndc, lot, expiry_date, quantity, location_id
            )
            SELECT hospital_id, facility_id, ndc,
                   'SEED-' || id::text,
                   (CURRENT_DATE + INTERVAL '365 days')::date,
                   quantity,
                   location_id
            FROM stock_snapshot
            WHERE facility_id IS NOT NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TRIGGER audit_stock_batch
              AFTER INSERT OR UPDATE OR DELETE ON stock_batch
              FOR EACH ROW EXECUTE FUNCTION write_audit_entry();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER audit_par_level
              AFTER INSERT OR UPDATE OR DELETE ON par_level
              FOR EACH ROW EXECUTE FUNCTION write_audit_entry();
            """
        )
    )

    for table in _HOSPITAL_ID_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"""
                CREATE POLICY tenant_isolation ON {table}
                  USING      (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)
                  WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)
                """
            )
        )

    op.execute(sa.text("ALTER TABLE storage_location ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE storage_location FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON storage_location
              USING (EXISTS (
                SELECT 1 FROM facility f
                WHERE f.id = storage_location.facility_id
                  AND f.hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid
              ))
              WITH CHECK (EXISTS (
                SELECT 1 FROM facility f
                WHERE f.id = storage_location.facility_id
                  AND f.hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid
              ))
            """
        )
    )
    op.execute(sa.text("ALTER TABLE location_condition ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE location_condition FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON location_condition
              USING (EXISTS (
                SELECT 1 FROM storage_location sl
                JOIN facility f ON f.id = sl.facility_id
                WHERE sl.id = location_condition.location_id
                  AND f.hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid
              ))
              WITH CHECK (EXISTS (
                SELECT 1 FROM storage_location sl
                JOIN facility f ON f.id = sl.facility_id
                WHERE sl.id = location_condition.location_id
                  AND f.hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid
              ))
            """
        )
    )

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_role') THEN
                CREATE ROLE app_role NOLOGIN;
              END IF;
            END
            $$
            """
        )
    )
    op.execute(sa.text("GRANT app_role TO CURRENT_USER"))
    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO app_role"))
    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_role"))
    op.execute(sa.text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role"))
    op.execute(
        sa.text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_role"
        )
    )
    op.execute(
        sa.text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES TO app_role"
        )
    )
    op.execute(sa.text("REVOKE UPDATE, DELETE ON audit_log_entry FROM app_role"))


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON location_condition"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON storage_location"))
    op.execute(sa.text("ALTER TABLE location_condition DISABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE storage_location DISABLE ROW LEVEL SECURITY"))
    for table in _HOSPITAL_ID_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_par_level ON par_level"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_stock_batch ON stock_batch"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS stock_batch_rollup ON stock_batch"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS stock_batch_rollup()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS refresh_stock_snapshot(uuid, bigint, text, text)"))
    op.drop_table("par_level")
    op.drop_index("ix_stock_batch_fefo", table_name="stock_batch")
    op.drop_table("stock_batch")
