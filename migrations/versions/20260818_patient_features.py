"""The four features the rules engine weighs most and the record could not hold.

Measured before this migration, across 200 seeded patients: sex, eGFR, hepatic
status and prior adverse reactions were present for **none** of them, because
`patient` had no columns for any of them. `patient_row_to_vector` could only
default them to "unknown", so three findings never fired for anybody:

    45 pts  PRIOR_ADR_SAME_CLASS   needs prior_adr_rxcuis
    30 pts  RENAL_DOSE_EXCEEDED    needs egfr_band
    20 pts  HEPATIC_IMPAIRED       needs hepatic

95 of the 220 weight points, including the heaviest finding in the table, on a
scale whose RED threshold is 60. Label extraction compounded it: PROGNOSIS_FEATURES
lets a risk factor be keyed on egfr_band, hepatic or sex, so we paid to extract
and a pharmacist paid to approve conditions that could never match a patient.

eGFR is stored as the lab's own value rather than a band. Bands belong to the
ruleset and `egfr_band_from_value` applies them at read time, so a re-scored
assessment reflects the measurement rather than whichever boundaries were
current when it was filed.

Revision ID: 20260818_feat
Revises: 20260818_note
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "20260818_feat"
down_revision: str | None = "20260818_note"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("patient")]
    if "sex" not in columns:
        op.add_column("patient", sa.Column("sex", sa.Text(), nullable=True))
    if "egfr_value" not in columns:
        op.add_column("patient", sa.Column("egfr_value", sa.Numeric(6, 2), nullable=True))
    if "hepatic" not in columns:
        op.add_column("patient", sa.Column("hepatic", sa.Text(), nullable=True))
    if "prior_adr_rxcuis" not in columns:
        op.add_column(
            "patient",
            sa.Column(
                "prior_adr_rxcuis", ARRAY(sa.Text()), nullable=False, server_default="{}"
            ),
        )
    # Which values a model read off a document rather than a clinician entering.
    # A transcribed creatinine and a typed one are not the same evidence, and
    # /explain has to be able to say which it was.
    if "feature_provenance" not in columns:
        op.add_column(
            "patient",
            sa.Column("feature_provenance", JSONB(), nullable=False, server_default="{}"),
        )
    # Nullable and unconstrained on purpose: 'unknown' is a real clinical state,
    # and a CHECK that forced M/F would make an unrecorded sex unrepresentable.
    constraints = [c["name"] for c in inspector.get_check_constraints("patient")]
    if "ck_patient_hepatic" not in constraints:
        op.create_check_constraint(
            "ck_patient_hepatic",
            "patient",
            "hepatic IS NULL OR hepatic IN ('normal','impaired','unknown')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    constraints = [c["name"] for c in inspector.get_check_constraints("patient")]
    if "ck_patient_hepatic" in constraints:
        op.drop_constraint("ck_patient_hepatic", "patient", type_="check")
    columns = [c["name"] for c in inspector.get_columns("patient")]
    for column in ("feature_provenance", "prior_adr_rxcuis", "hepatic", "egfr_value", "sex"):
        if column in columns:
            op.drop_column("patient", column)
