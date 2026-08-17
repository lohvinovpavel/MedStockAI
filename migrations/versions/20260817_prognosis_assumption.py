"""PP-4 procurement: prognosis_assumption.

Reference class (services.md §1.1) — model parameters, not tenant data, so no
hospital_id and no RLS.

One row seeded: switch_rate. It is deliberately data rather than a literal,
because it is the one number in the forecast nobody has measured
(docs/prognosis-and-procurement.md §2.2).

Revision ID: 20260817_assume
Revises: 20260817_warehouse
Create Date: 2026-08-17

Re-pointed from 20260814_prog onto 20260817_warehouse after rebasing on main.
Warehouse (PR #29) was written against prog as its parent too, which left
alembic with two heads and `upgrade head` refusing to pick one — the same break
20260814_prognosis.py already carries a note about. This branch is the unmerged
one, so it moves; warehouse is on main and may already be stamped in deployed
databases. The tables are independent, so only the linearity matters.

The revision id stays `20260817_assume` — alembic orders by the down_revision
chain, not by the name, and the id is already stamped in dev databases.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_assume"
down_revision: str | None = "20260817_warehouse"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prognosis_assumption",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_prognosis_assumption"),
        sa.UniqueConstraint("name", name="uq_prognosis_assumption_name"),
    )

    # Seeded here rather than in a seed script: the forecast cannot run without
    # it, and a missing row would make /forecast fail on a fresh database for a
    # reason that reads like a bug rather than a missing fixture.
    op.execute(
        sa.text(
            """
            INSERT INTO prognosis_assumption (name, value, note)
            VALUES (
                'switch_rate',
                0.6,
                'Assumed share of flagged patients a pharmacist actually '
                'switches. Not measured -- chosen. Change it here rather than '
                'in code, and read it off /ruleset wherever the forecast shows.'
            )
            ON CONFLICT (name) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_table("prognosis_assumption")
