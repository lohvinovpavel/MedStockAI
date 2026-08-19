"""The record has to be able to hold what the ruleset weighs.

Measured across 200 seeded patients before this existed: sex, eGFR, hepatic
status and prior adverse reactions were present for **none** of them, because
`patient` had no columns for any of them. `patient_row_to_vector` could only
default them to "unknown", and three findings could never fire for anybody:

    45 pts  PRIOR_ADR_SAME_CLASS   needs prior_adr_rxcuis
    30 pts  RENAL_DOSE_EXCEEDED    needs egfr_band
    20 pts  HEPATIC_IMPAIRED       needs hepatic

95 of 220 weight points on a scale whose RED threshold is 60, including the
heaviest finding in the table. Every assessment still returned a verdict, every
test passed, and nothing anywhere said the engine was running on 57% of itself.

That is the failure mode these guard: not a wrong answer, but a confident answer
computed from features the record cannot supply. A weight added to the table with
no column behind it is the same bug again, so the reachability test is written
against `WEIGHTS` rather than against a list of names.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from medstock_shared.models import Patient
from medstock_shared.patient import (
    EGFR_BAND_FLOORS,
    WEIGHTS,
    PatientVector,
    egfr_band_from_value,
    patient_row_to_vector,
)

# Which column each weighted finding ultimately reads. A finding whose feature
# the record cannot store is unreachable no matter what the ruleset says.
FINDING_REQUIRES: dict[str, str | None] = {
    "PRIOR_ADR_SAME_CLASS": "prior_adr_rxcuis",
    "RENAL_DOSE_EXCEEDED": "egfr_value",
    "HEPATIC_IMPAIRED": "hepatic",
    "AGE_INAPPROPRIATE": "date_of_birth",
    # Needs a coded diagnosis on the record. Only 19% of the cohort carries one,
    # so this fires for a minority -- which is correct, not a coverage gap: a
    # patient with no recorded condition has no condition to be worsened.
    "CONDITION_WORSENED": "condition_codes",
    "DUPLICATE_CLASS": None,          # from the cart, not the record
    "INTERACTION_MAJOR": None,
    "INTERACTION_MODERATE": None,
    "NARROW_THERAPEUTIC_INDEX": None,
    "ADR_SIGNAL": None,               # population signal, patient-independent
    "ADR_SIGNAL_STRONG": None,
}


def populated_row(**overrides) -> SimpleNamespace:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "date_of_birth": date(1950, 1, 1),
        "sex": "F",
        "egfr_value": 25,
        "hepatic": "impaired",
        "prior_adr_rxcuis": ["29046"],
        "allergy_codes": [],
        "condition_codes": [],
        "pgx_phenotypes": [],
    }
    return SimpleNamespace(**{**base, **overrides})


# --- the reachability guard ---------------------------------------------------


def test_every_weighted_finding_has_a_column_behind_it():
    """The test that would have caught the original defect.

    Written against WEIGHTS so that adding a finding whose feature the record
    cannot store fails here, rather than shipping as a rule that never fires.
    """
    columns = {c.name for c in Patient.__table__.columns}
    missing = {
        finding: needs
        for finding, needs in FINDING_REQUIRES.items()
        if needs is not None and needs not in columns
    }
    assert not missing, (
        f"findings that can never fire, because patient has no column for them: {missing}"
    )


def test_the_map_covers_the_whole_weight_table():
    """Otherwise a new weight silently escapes the check above."""
    assert set(WEIGHTS) == set(FINDING_REQUIRES), (
        "FINDING_REQUIRES must name every weighted finding; "
        f"unmapped: {set(WEIGHTS) - set(FINDING_REQUIRES)}"
    )


# --- the vector actually carries them -----------------------------------------


@pytest.mark.parametrize(
    "field,expected",
    [
        ("sex", "F"),
        ("egfr_band", "15-29"),
        ("hepatic", "impaired"),
        ("prior_adr_rxcuis", ("29046",)),
    ],
)
def test_the_vector_reads_the_feature_off_the_row(field, expected):
    assert getattr(patient_row_to_vector(populated_row()), field) == expected


def test_a_bare_row_still_maps_without_raising():
    """An older row has none of these. It must degrade to "unknown", which is a
    real clinical state, not crash the assessment."""
    row = SimpleNamespace(id="x", date_of_birth=date(1980, 5, 5), allergy_codes=[])
    vector = patient_row_to_vector(row)
    assert vector.egfr_band == "unknown"
    assert vector.hepatic == "unknown"
    assert vector.prior_adr_rxcuis == ()


# --- banding ------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,band",
    [
        (95, ">=90"), (90, ">=90"),          # boundary is inclusive below
        (89.9, "60-89"), (60, "60-89"),
        (59, "45-59"), (45, "45-59"),
        (44, "30-44"), (30, "30-44"),
        (29, "15-29"), (15, "15-29"),
        (14.9, "<15"), (0, "<15"),
    ],
)
def test_egfr_bands_at_their_boundaries(value, band):
    """KDIGO boundaries, checked on both sides. An off-by-one here moves a
    patient between renal bands and changes whether a dose finding fires."""
    assert egfr_band_from_value(value) == band


@pytest.mark.parametrize("value", [None, "", "not a number", -1])
def test_an_unusable_egfr_is_unknown_not_an_exception(value):
    """A lab value that failed to parse must read as absent. Guessing a band
    from a bad value would be worse than having none."""
    assert egfr_band_from_value(value) == "unknown"


def test_the_bands_are_the_vocabulary_the_extractor_is_given():
    """PROGNOSIS_FEATURES offers the label extractor these exact strings. If the
    two drift, a factor extracted from a label can never match a patient."""
    from medstock_shared.ai_tasks import PROGNOSIS_FEATURES

    produced = {band for _floor, band in EGFR_BAND_FLOORS} | {"<15"}
    assert produced == PROGNOSIS_FEATURES["egfr_band"]


def test_banding_is_derived_not_stored():
    """The measurement outlives the boundaries. A profile re-scored after a band
    is redrawn should reflect the eGFR, not the band current when it was filed."""
    assert "egfr_band" not in {c.name for c in Patient.__table__.columns}
    assert "egfr_value" in {c.name for c in Patient.__table__.columns}


# --- provenance ---------------------------------------------------------------


def test_the_record_can_say_a_value_was_extracted():
    """A transcribed creatinine and a clinician-typed one are the same number
    and not the same evidence; /explain has to be able to tell them apart."""
    assert "feature_provenance" in {c.name for c in Patient.__table__.columns}


def test_the_vector_carries_no_provenance():
    """Provenance is about the record, not the question. It must not reach the
    de-identified vector, and it must not enter the feature hash."""
    assert not hasattr(PatientVector(), "feature_provenance")
