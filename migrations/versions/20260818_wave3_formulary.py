"""Wave 3: H1 audit on formulary_item (B6 writes go through session_scope),
index shortage_event.ndc for the B3 exposure join.

formulary_item already exists (UC-1). RLS was applied in wave 2. This revision
is the wave-3 head — no new tenant table, so no new policy.

Revision ID: 20260818_wave3
Revises: 20260818_wave2
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_wave3"
down_revision: str | None = "20260818_wave2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TRIGGER audit_formulary_item
              AFTER INSERT OR UPDATE OR DELETE ON formulary_item
              FOR EACH ROW EXECUTE FUNCTION write_audit_entry();
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_shortage_event_ndc ON shortage_event (ndc)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_shortage_event_ndc"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_formulary_item ON formulary_item"))
