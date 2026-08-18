"""Wave 1 H1: append-only audit_log_entry, review_decision, trigger, RLS.

Trigger is attached only to review_decision. Other tables listed in the H1
spec either do not exist yet or are written by seeds without an actor
(formulary_item, drug_certification) — attaching them would fail the CHECK.

RLS FORCE on these two tables only (the rest of A4 is wave 2). GET /audit
SETs LOCAL ROLE app_role so a superuser connection cannot bypass the policy.

Revision ID: 20260818_h1_audit
Revises: 20260818_hospital_uuid
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260818_h1_audit"
down_revision: str | None = "20260818_hospital_uuid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_decision",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_ref", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), server_default="pending", nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "entity_type IN ('restock_recommendation','analogue_substitution')",
            name="ck_review_decision_entity_type",
        ),
        sa.CheckConstraint(
            "decision IN ('pending','approved','rejected')",
            name="ck_review_decision_decision",
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospital.id"], name="fk_review_decision_hospital_id_hospital"),
        sa.ForeignKeyConstraint(["facility_id"], ["facility.id"], name="fk_review_decision_facility_id_facility"),
        sa.PrimaryKeyConstraint("id", name="pk_review_decision"),
    )

    op.create_table(
        "audit_log_entry",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hospital_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_system", sa.Text(), nullable=True),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("before", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ai_dedupe_key", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_id IS NOT NULL OR actor_system IS NOT NULL",
            name="ck_audit_log_entry_actor",
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospital.id"], name="fk_audit_log_entry_hospital_id_hospital"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log_entry"),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_audit_entity ON audit_log_entry "
            "(hospital_id, entity_type, entity_id, occurred_at DESC)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_audit_time ON audit_log_entry "
            "(hospital_id, occurred_at DESC)"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE FUNCTION write_audit_entry() RETURNS trigger AS $BODY$
            BEGIN
              INSERT INTO audit_log_entry (
                hospital_id, actor_id, actor_system, entity_type, entity_id,
                action, before, after, ai_dedupe_key
              )
              VALUES (
                current_setting('app.hospital_id', true)::uuid,
                nullif(current_setting('app.actor_id', true), '')::uuid,
                nullif(current_setting('app.actor_system', true), ''),
                TG_TABLE_NAME,
                COALESCE(NEW.id, OLD.id)::text,
                TG_OP,
                CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
                CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END,
                nullif(current_setting('app.ai_dedupe_key', true), '')
              );
              RETURN COALESCE(NEW, OLD);
            END;
            $BODY$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER audit_review_decision
              AFTER INSERT OR UPDATE ON review_decision
              FOR EACH ROW EXECUTE FUNCTION write_audit_entry();
            """
        )
    )

    for table in ("review_decision", "audit_log_entry"):
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
    # Whoever ran this migration (CI/docker: medstock) must be able to
    # SET LOCAL ROLE app_role, which GET /audit does so a superuser
    # connection cannot bypass FORCE RLS.
    op.execute(sa.text("GRANT app_role TO CURRENT_USER"))
    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO app_role"))
    op.execute(sa.text("GRANT SELECT, INSERT ON review_decision TO app_role"))
    op.execute(sa.text("GRANT UPDATE ON review_decision TO app_role"))
    op.execute(sa.text("GRANT SELECT, INSERT ON audit_log_entry TO app_role"))
    op.execute(sa.text("REVOKE UPDATE, DELETE ON audit_log_entry FROM app_role"))
    op.execute(sa.text("GRANT USAGE, SELECT ON SEQUENCE review_decision_id_seq TO app_role"))
    op.execute(sa.text("GRANT USAGE, SELECT ON SEQUENCE audit_log_entry_id_seq TO app_role"))


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_review_decision ON review_decision"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS write_audit_entry()"))
    op.drop_index("ix_audit_time", table_name="audit_log_entry")
    op.drop_index("ix_audit_entity", table_name="audit_log_entry")
    op.drop_table("audit_log_entry")
    op.drop_table("review_decision")
