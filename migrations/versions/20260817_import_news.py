"""COMP import certification and news: import_alert, news_signal.

docs/compliance-usecases.md §5. Both reference class — an import alert is about
a manufacturer and an article is about a drug; neither is about a hospital.

`import_alert.firm_key` is the normalised name the labeler match runs on. It is
stored rather than computed on read so the match is indexable and so the exact
normalisation that produced it can be inspected when a match is disputed —
which matters because this is the one finding in the system that names a
company rather than describing a product.

Revision ID: 20260817_comp
Revises: 20260817_adr
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_comp"
down_revision: str | None = "20260817_adr"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_alert",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("alert_number", sa.Text(), nullable=False),
        sa.Column("firm_name", sa.Text(), nullable=False),
        sa.Column("firm_key", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("listed_at", sa.Date(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_alert"),
        sa.UniqueConstraint("alert_number", "firm_name", name="uq_import_alert_natural"),
    )
    op.create_index("ix_import_alert_number", "import_alert", ["alert_number"])
    # The lookup is "is this labeler listed", so the normalised name is the index.
    op.create_index("ix_import_alert_firm_key", "import_alert", ["firm_key"])

    op.create_table(
        "news_signal",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ndc", sa.Text(), nullable=True),
        sa.Column("query_term", sa.Text(), nullable=False, server_default=""),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_news_signal"),
        # One row per article. The same story syndicated to twenty outlets is
        # twenty rows by design — they are different sources making the same
        # claim, and collapsing them would hide how thin a signal is.
        sa.UniqueConstraint("url", name="uq_news_signal_url"),
    )
    op.create_index("ix_news_signal_ndc", "news_signal", ["ndc"])


def downgrade() -> None:
    op.drop_index("ix_news_signal_ndc", table_name="news_signal")
    op.drop_table("news_signal")
    op.drop_index("ix_import_alert_firm_key", table_name="import_alert")
    op.drop_index("ix_import_alert_number", table_name="import_alert")
    op.drop_table("import_alert")
