"""ai_cache gains prompt_version and model_name.

docs/ai-module-plan.md §2/§4 Phase 1. Widens the uniqueness of a cached
answer from (type, dedupe_key) to (type, prompt_version, dedupe_key), so that
bumping AITask.prompt_version on a prompt edit invalidates its own cache row
instead of an old prompt's answer being replayed as if the new prompt had
produced it.

Existing rows are backfilled prompt_version='v1' (the default every AITask
had before this column existed) and model_name='unknown' (the model that
produced them was never recorded, so this is an honest "we don't know" rather
than a guess presented as fact).

Revision ID: 20260818_ai_cache_ver
Revises: 20260817_comp
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_ai_cache_ver"
down_revision: str | None = "20260817_comp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop first: the old constraint's name is reused nowhere, but the widened
    # one below can't coexist with it (both cover `type`+`dedupe_key`), so the
    # add-columns-then-widen sequence has to start by clearing the old shape.
    op.drop_constraint("uq_ai_cache_type_dedupe", "ai_cache", type_="unique")

    op.add_column(
        "ai_cache",
        sa.Column("prompt_version", sa.Text(), nullable=False, server_default="v1"),
    )
    op.add_column(
        "ai_cache",
        sa.Column("model_name", sa.Text(), nullable=False, server_default="unknown"),
    )

    op.create_unique_constraint(
        "uq_ai_cache_type_promptver_dedupe",
        "ai_cache",
        ["type", "prompt_version", "dedupe_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_ai_cache_type_promptver_dedupe", "ai_cache", type_="unique")
    op.drop_column("ai_cache", "model_name")
    op.drop_column("ai_cache", "prompt_version")
    op.create_unique_constraint(
        "uq_ai_cache_type_dedupe", "ai_cache", ["type", "dedupe_key"]
    )
