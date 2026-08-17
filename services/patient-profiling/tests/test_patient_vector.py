"""Unit tests for demo patient → vector mapping and avoided-ingredient warnings."""

from datetime import date
from types import SimpleNamespace

from medstock_shared.patient import (
    PatientVector,
    age_band_from_dob,
    avoided_ingredient_warnings,
    patient_row_to_vector,
)


def test_age_band_from_dob():
    assert age_band_from_dob(date(1990, 1, 1), today=date(2026, 8, 15)) == "18-39"
    assert age_band_from_dob(date(1980, 1, 1), today=date(2026, 8, 15)) == "40-64"
    assert age_band_from_dob(date(1950, 1, 1), today=date(2026, 8, 15)) == "75-89"


def test_patient_row_to_vector_strips_phi():
    row = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        full_name="Elena Vasquez",
        date_of_birth=date(1978, 4, 12),
        allergy_codes=["caffeine"],
        condition_codes=["avoid_caffeine"],
    )
    v = patient_row_to_vector(row)
    assert not hasattr(v, "full_name")
    assert v.patient_ref == str(row.id)
    assert v.age_band == age_band_from_dob(row.date_of_birth)
    assert "caffeine" in v.allergy_codes
    assert "avoid_caffeine" in v.condition_codes


def test_avoided_ingredient_warnings_caffeine():
    v = PatientVector(condition_codes=("avoid_caffeine",))
    ingredients = [{"rxcui": "1886", "name": "Caffeine"}, {"rxcui": "1191", "name": "Aspirin"}]
    findings = avoided_ingredient_warnings(v, "198479", ingredients)
    assert any(f.code == "AVOIDED_INGREDIENT" for f in findings)
    assert not avoided_ingredient_warnings(v, "246461", [{"rxcui": "1191", "name": "Aspirin"}])
