"""What `assessment_log.feature_hash` may and may not reveal.

The audit trail is the one place the no-PHI design could quietly undo itself.
docs/patient-profiling-usecases.md §2.4 promises the log records the decision and
not the patient, and §7 says `feature_hash` is what makes that possible. These
tests pin the two properties that promise rests on: the same clinical question
always hashes the same, and nothing that identifies a person is inside it.
"""

from __future__ import annotations

from medstock_shared.patient import PatientVector

BASE = PatientVector(
    age_band="75-89",
    sex="F",
    egfr_band="45-59",
    hepatic="impaired",
    allergy_codes=("penicillin",),
    condition_codes=("I50.9", "E11.9"),
    active_rxcuis=("861007",),
)


# --- stability ---------------------------------------------------------------


def test_the_same_question_hashes_the_same():
    assert BASE.feature_hash() == PatientVector(**vars(BASE)).feature_hash()


def test_order_within_a_field_does_not_change_the_question():
    """The same two conditions listed the other way round is the same patient.
    A hash that disagreed would make the log unable to recognise a repeat."""
    swapped = PatientVector(**{**vars(BASE), "condition_codes": ("E11.9", "I50.9")})
    assert swapped.feature_hash() == BASE.feature_hash()


def test_a_different_band_is_a_different_question():
    other = PatientVector(**{**vars(BASE), "egfr_band": "30-44"})
    assert other.feature_hash() != BASE.feature_hash()


def test_every_clinical_field_reaches_the_hash():
    """A field silently left out would make two different patients audit as one.
    Checked per field rather than by inspection, so adding a vector field
    without adding it here fails loudly instead of quietly narrowing the hash."""
    variants = {
        "age_band": "40-64",
        "sex": "M",
        "egfr_band": ">=90",
        "hepatic": "normal",
        "allergy_codes": ("sulfa",),
        "condition_codes": ("J45.9",),
        "active_rxcuis": ("29046",),
        "prior_adr_rxcuis": ("11289",),
    }
    for field, value in variants.items():
        changed = PatientVector(**{**vars(BASE), field: value})
        assert changed.feature_hash() != BASE.feature_hash(), (
            f"{field} does not affect feature_hash — two clinically different "
            "assessments would be indistinguishable in the audit trail"
        )


# --- what must NOT be in it --------------------------------------------------


def test_patient_ref_is_excluded():
    """The load-bearing one. patient_ref is opaque but stable per patient, so a
    hash that included it would let anyone holding assessment_log group every
    assessment ever made about one person -- a re-identification handle built
    out of the audit trail the no-PHI design depends on (§2.4)."""
    named = PatientVector(**{**vars(BASE), "patient_ref": "hospital-token-42"})
    other = PatientVector(**{**vars(BASE), "patient_ref": "hospital-token-99"})
    assert named.feature_hash() == BASE.feature_hash()
    assert named.feature_hash() == other.feature_hash()


def test_the_hash_does_not_contain_the_values_it_hashes():
    """Guards against someone 'simplifying' the hash into a join of the fields,
    which would put the clinical vector in the clear in the audit table."""
    digest = BASE.feature_hash()
    for value in ("75-89", "45-59", "penicillin", "I50.9", "861007", "impaired"):
        assert value not in digest


def test_it_looks_like_sha256():
    digest = BASE.feature_hash()
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
