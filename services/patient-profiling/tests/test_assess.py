"""PP-1 assessment rules and PP-2 demand planning. Pure functions, no database,
no network, no model — the point of a deterministic pipeline is that it can be
pinned down exactly like this.
"""

import pytest
from medstock_shared.patient import (
    BANDS,
    WEIGHTS,
    PatientVector,
    Severity,
    Verdict,
    assess,
    best_substitute,
    plan_demand,
)

AMOXICILLIN = "308182"  # penicillin
ATORVASTATIN = "617310"  # statin
SIMVASTATIN = "36567"  # statin
LISINOPRIL = "314076"  # ACE inhibitor
ENALAPRIL = "29046"  # ACE inhibitor
METFORMIN = "861007"  # biguanide
ASPIRIN = "246461"  # NSAID
MIDAZOLAM = "311700"  # benzodiazepine
WARFARIN = "11289"  # anticoagulant

HEALTHY = PatientVector(age_band="40-64", egfr_band=">=90", hepatic="normal")


def codes(result) -> set[str]:
    return {f.code for f in result.findings}


# --- intake -----------------------------------------------------------------


def test_identifiers_are_dropped_not_stored():
    """The rejection is the boundary: a hospital cannot accidentally send us a
    name (docs/phi-readiness.md §4)."""
    v = PatientVector.from_json(
        {
            "full_name": "Ada Reyes",
            "mrn": "MRN100001",
            "date_of_birth": "1948-03-02",
            "postcode": "10115",
            "patient_ref": "SYN-00001",
            "age_band": "75-89",
            "active_rxcuis": ["11289"],
        }
    )
    assert not hasattr(v, "full_name")
    assert v.patient_ref == "SYN-00001"
    assert v.age_band == "75-89"
    assert v.active_rxcuis == ("11289",)


def test_missing_fields_become_unknown_not_assumed_healthy():
    v = PatientVector.from_json({})
    assert v.egfr_band == "unknown"
    assert v.hepatic == "unknown"


# --- hard gates -------------------------------------------------------------


def test_allergy_to_the_same_class_blocks():
    v = PatientVector(allergy_codes=("penicillin",))
    result = assess(v, AMOXICILLIN)
    assert result.verdict is Verdict.BLOCKED
    assert "ALLERGY_MATCH" in codes(result)


def test_a_block_produces_no_score():
    """A number beside an absolute contraindication invites someone to weigh it
    against a discount."""
    result = assess(PatientVector(allergy_codes=("penicillin",)), AMOXICILLIN)
    assert result.score is None


def test_already_on_the_exact_drug_blocks():
    result = assess(PatientVector(active_rxcuis=(METFORMIN,)), METFORMIN)
    assert result.verdict is Verdict.BLOCKED
    assert "DUPLICATE_INGREDIENT" in codes(result)


def test_a_block_short_circuits_the_later_stages():
    v = PatientVector(age_band="90+", egfr_band="<15", allergy_codes=("penicillin",))
    result = assess(v, AMOXICILLIN)
    assert result.stages_completed == (1, 2, 3, 4)


def test_an_unrelated_allergy_does_not_block():
    assert assess(PatientVector(allergy_codes=("sulfa",)), AMOXICILLIN).verdict is not Verdict.BLOCKED


# --- graded findings --------------------------------------------------------


def test_major_interaction_scores_higher_than_moderate():
    major = assess(PatientVector(active_rxcuis=(WARFARIN,)), ASPIRIN)
    moderate = assess(PatientVector(active_rxcuis=(WARFARIN,)), ATORVASTATIN)
    assert major.score > moderate.score
    assert "INTERACTION_MAJOR" in codes(major)
    assert "INTERACTION_MODERATE" in codes(moderate)


def test_renal_floor_fires_below_the_band():
    bad = assess(PatientVector(egfr_band="15-29"), METFORMIN)
    ok = assess(PatientVector(egfr_band=">=90"), METFORMIN)
    assert "RENAL_DOSE_EXCEEDED" in codes(bad)
    assert "RENAL_DOSE_EXCEEDED" not in codes(ok)


def test_unknown_kidney_function_is_reported_not_assumed_fine():
    """Green must never quietly mean 'we did not check'."""
    result = assess(PatientVector(egfr_band="unknown"), METFORMIN)
    assert "RENAL_UNKNOWN" in codes(result)
    assert all(f.weight == 0 for f in result.findings if f.code == "RENAL_UNKNOWN")


def test_age_rule_applies_only_to_older_bands():
    old = assess(PatientVector(age_band="75-89"), MIDAZOLAM)
    young = assess(PatientVector(age_band="40-64"), MIDAZOLAM)
    assert "AGE_INAPPROPRIATE" in codes(old)
    assert "AGE_INAPPROPRIATE" not in codes(young)


def test_prior_adr_to_the_class_is_the_heaviest_weight():
    result = assess(PatientVector(prior_adr_rxcuis=(SIMVASTATIN,)), ATORVASTATIN)
    assert "PRIOR_ADR_SAME_CLASS" in codes(result)
    assert WEIGHTS["PRIOR_ADR_SAME_CLASS"] == max(WEIGHTS.values())


def test_duplicate_class_is_flagged_without_blocking():
    result = assess(PatientVector(active_rxcuis=(ATORVASTATIN,)), SIMVASTATIN)
    assert result.verdict is not Verdict.BLOCKED
    assert "DUPLICATE_CLASS" in codes(result)


# --- scoring ----------------------------------------------------------------


def test_a_clean_patient_is_green_with_no_findings():
    result = assess(HEALTHY, ATORVASTATIN)
    assert result.verdict is Verdict.GREEN
    assert result.score == 0


def test_one_concern_is_noise_two_is_a_pattern():
    """The band thresholds encode exactly that, so check both sides of it."""
    assert BANDS[1][0] == 30
    # Age caution on an NSAID alone: 20 points, still green.
    single = assess(PatientVector(age_band="75-89"), ASPIRIN)
    assert single.score == WEIGHTS["AGE_INAPPROPRIATE"]
    assert single.verdict is Verdict.GREEN
    # Same patient already on another NSAID: +25 duplicate class -> amber.
    both = assess(PatientVector(age_band="75-89", active_rxcuis=("197603",)), ASPIRIN)
    assert both.score == WEIGHTS["AGE_INAPPROPRIATE"] + WEIGHTS["DUPLICATE_CLASS"]
    assert both.verdict is Verdict.AMBER


def test_score_is_the_sum_of_its_findings():
    v = PatientVector(age_band="75-89", egfr_band="15-29", active_rxcuis=(WARFARIN,))
    result = assess(v, ASPIRIN)
    assert result.score == sum(f.weight for f in result.findings)


def test_the_same_input_always_gives_the_same_answer():
    v = PatientVector(age_band="75-89", egfr_band="30-44", active_rxcuis=(WARFARIN,))
    assert assess(v, ASPIRIN) == assess(v, ASPIRIN)


def test_every_non_info_finding_carries_a_source_and_stage():
    v = PatientVector(age_band="75-89", egfr_band="15-29", active_rxcuis=(WARFARIN,))
    for f in assess(v, ASPIRIN).findings:
        assert f.source and f.message and f.stage >= 1


def test_an_unmapped_drug_says_so_rather_than_scoring_zero_silently():
    result = assess(HEALTHY, "999999")
    assert "DRUG_CLASS_UNKNOWN" in codes(result)
    assert result.findings[0].severity is Severity.INFO


# --- substitution -----------------------------------------------------------


def test_substitution_stays_within_the_therapeutic_class():
    """A drug being safe is not a reason to prescribe it."""
    alt, _ = best_substitute(HEALTHY, LISINOPRIL, [ENALAPRIL, ASPIRIN, METFORMIN], set())
    assert alt == ENALAPRIL


def test_no_in_class_alternative_returns_a_reason():
    alt, reason = best_substitute(HEALTHY, METFORMIN, [ASPIRIN, ENALAPRIL], set())
    assert alt is None
    assert "biguanide" in reason


def test_a_blocked_alternative_is_not_offered():
    allergic = PatientVector(allergy_codes=("penicillin",))
    alt, _ = best_substitute(allergic, AMOXICILLIN, [AMOXICILLIN], set())
    assert alt is None


# --- demand planning --------------------------------------------------------


def cohort():
    return [
        PatientVector(age_band="65-74", egfr_band="60-89", active_rxcuis=(METFORMIN,)),
        PatientVector(age_band="40-64", egfr_band=">=90", active_rxcuis=(LISINOPRIL,)),
        PatientVector(age_band="75-89", egfr_band="45-59", active_rxcuis=(LISINOPRIL, METFORMIN)),
        PatientVector(age_band="40-64", egfr_band=">=90"),  # on nothing we stock
    ]


CANDIDATES = [METFORMIN, LISINOPRIL, ENALAPRIL, ATORVASTATIN, ASPIRIN]


def test_demand_follows_therapy_not_eligibility():
    """Everyone here can tolerate atorvastatin; nobody is on it, so we buy none."""
    lines, _ = plan_demand(cohort(), CANDIDATES)
    assert lines[ATORVASTATIN].eligible == 4
    assert lines[ATORVASTATIN].units_needed == 0
    assert lines[METFORMIN].on_therapy == 2
    assert lines[METFORMIN].units_needed == 60


def test_a_patient_on_nothing_we_stock_generates_no_demand():
    """Four stocked therapies across the cohort — the fourth patient is on none
    of them and contributes nothing, even though every drug is safe for them."""
    lines, _ = plan_demand(cohort(), CANDIDATES)
    assert sum(x.units_needed for x in lines.values()) == 4 * 30


def test_withdrawing_a_drug_moves_its_demand_in_class():
    before, _ = plan_demand(cohort(), CANDIDATES)
    after, unserved = plan_demand(cohort(), CANDIDATES, unavailable=[LISINOPRIL])
    assert after[LISINOPRIL].units_needed == 0
    assert after[ENALAPRIL].units_needed == before[LISINOPRIL].units_needed
    assert after[ENALAPRIL].substitutes_for == 2
    assert not unserved


def test_patients_with_no_alternative_are_counted_not_lost():
    after, unserved = plan_demand(cohort(), CANDIDATES, unavailable=[METFORMIN])
    assert after[METFORMIN].units_needed == 0
    assert sum(unserved.values()) == 2
    assert "biguanide" in next(iter(unserved))


def test_shortfall_is_demand_minus_stock():
    lines, _ = plan_demand(cohort(), CANDIDATES, on_hand={METFORMIN: 40})
    assert lines[METFORMIN].units_needed == 60
    assert lines[METFORMIN].shortfall == 20


def test_stock_beyond_demand_is_not_negative_shortfall():
    lines, _ = plan_demand(cohort(), CANDIDATES, on_hand={METFORMIN: 5000})
    assert lines[METFORMIN].shortfall == 0


@pytest.mark.parametrize("units", [1, 30, 90])
def test_units_per_patient_scales_linearly(units):
    lines, _ = plan_demand(cohort(), CANDIDATES, units_per_patient=units)
    assert sum(x.units_needed for x in lines.values()) == 4 * units


def test_block_reasons_are_recorded_per_drug():
    allergic = [PatientVector(allergy_codes=("penicillin",), active_rxcuis=(AMOXICILLIN,))]
    lines, _ = plan_demand(allergic, [AMOXICILLIN, ENALAPRIL])
    assert lines[AMOXICILLIN].blocked == 1
    assert lines[AMOXICILLIN].reasons["ALLERGY_MATCH"] == 1


def test_the_plan_does_not_drift_between_identical_runs():
    assert plan_demand(cohort(), CANDIDATES) == plan_demand(cohort(), CANDIDATES)
