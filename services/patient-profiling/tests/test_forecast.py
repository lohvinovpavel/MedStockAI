"""PP-4: the four numbers, and the arithmetic that turns them into a projection.

docs/prognosis-and-procurement.md §2. What separates this from /plan is that it
answers where therapy is *heading*, so these tests are about the difference
between a count and a projection -- and about keeping an assumption visible.

The rxcuis below are the ones that actually carry a DRUG_CLASS mapping. That
matters: substitution is in-class, so a drug with no class can never receive
switch_in, and a test written on an unmapped rxcui asserts zero for a reason
that has nothing to do with the behaviour it claims to check.
"""

from __future__ import annotations

import pytest
from medstock_shared.patient import (
    DemandLine,
    PatientVector,
    RiskProfile,
    Verdict,
    assess,
    assess_current_therapy,
    plan_demand,
)

LISINOPRIL = "29046"
ENALAPRIL = "314076"  # same ace_inhibitor class as lisinopril
METFORMIN = "861007"
ATORVASTATIN = "617310"
SIMVASTATIN = "36567"

CANDIDATES = [LISINOPRIL, ENALAPRIL, METFORMIN, ATORVASTATIN, SIMVASTATIN]


def cohort():
    """Third patient is old and renally impaired -- the case §2.1 is about."""
    return [
        PatientVector(age_band="65-74", egfr_band="60-89", active_rxcuis=(METFORMIN,)),
        PatientVector(age_band="40-64", egfr_band=">=90", active_rxcuis=(LISINOPRIL,)),
        PatientVector(
            age_band="75-89", egfr_band="45-59", active_rxcuis=(LISINOPRIL, METFORMIN)
        ),
        PatientVector(age_band="40-64", egfr_band=">=90"),
    ]


def fatal_renal_profile(rxcui: str) -> RiskProfile:
    """Seriousness matters to the arithmetic: PROGNOSIS_BASE scores fatal at 40
    and serious at 25, while the amber band starts at 30. A serious profile
    alone therefore leaves the verdict green and contributes weight without
    flagging -- correct, but it makes for a test that proves nothing on its own.
    """
    return RiskProfile(
        rxcui=rxcui,
        reaction="angioedema",
        seriousness="fatal",
        risk_factors=({"feature": "egfr_band", "op": "at_or_below", "value": "45-59"},),
        citation="Warnings and Precautions",
    )


# --- current therapy is not a new prescription ------------------------------


def test_a_drug_the_patient_is_on_does_not_block_as_a_duplicate():
    """The bug this helper exists for. assess() answers "should we start this
    drug", so asked about current therapy it blocks every time on
    DUPLICATE_INGREDIENT -- which as a risk signal reads "100% of patients are
    at risk". That is the question asked wrong, not a finding."""
    patient = cohort()[1]
    assert assess(patient, LISINOPRIL).verdict is Verdict.BLOCKED
    assert assess_current_therapy(patient, LISINOPRIL).verdict is not Verdict.BLOCKED


def test_removing_the_drug_leaves_the_rest_of_the_regimen_intact():
    """Only the drug under assessment is removed. Interactions with everything
    else must still be checked -- a drug cannot interact with itself."""
    patient = cohort()[2]  # on lisinopril and metformin
    result = assess_current_therapy(patient, LISINOPRIL)
    assert "DUPLICATE_INGREDIENT" not in [f.code for f in result.findings]


def test_at_risk_is_not_merely_on_therapy_restated():
    """The regression guard. at_risk was equal to on_therapy for every drug
    because every on-therapy assessment blocked as a duplicate."""
    lines, _ = plan_demand(cohort(), CANDIDATES)
    assert lines[LISINOPRIL].on_therapy == 2
    assert lines[LISINOPRIL].at_risk == 0
    assert lines[METFORMIN].on_therapy == 2
    assert lines[METFORMIN].at_risk == 0


# --- PP-3 feeding PP-4 ------------------------------------------------------


def test_an_approved_profile_raises_at_risk():
    """§3: one extraction serves both the per-patient answer and the forecast."""
    before, _ = plan_demand(cohort(), CANDIDATES)
    after, _ = plan_demand(
        cohort(), CANDIDATES, risk_profiles=[fatal_renal_profile(LISINOPRIL)]
    )
    assert before[LISINOPRIL].at_risk == 0
    assert after[LISINOPRIL].at_risk == 1  # only the renally impaired patient


def test_only_the_patient_who_matches_the_factors_is_flagged():
    """Two patients are on lisinopril; one has egfr 45-59 and one does not."""
    lines, _ = plan_demand(
        cohort(), CANDIDATES, risk_profiles=[fatal_renal_profile(LISINOPRIL)]
    )
    assert lines[LISINOPRIL].on_therapy == 2
    assert lines[LISINOPRIL].at_risk == 1


def test_a_profile_for_another_drug_does_not_touch_this_one():
    """RiskProfile.rxcui is a key, not a suggestion."""
    lines, _ = plan_demand(
        cohort(), CANDIDATES, risk_profiles=[fatal_renal_profile(METFORMIN)]
    )
    assert lines[LISINOPRIL].at_risk == 0
    assert lines[METFORMIN].at_risk == 1


def test_profiles_do_not_change_who_is_on_therapy():
    """A prognosis colours a patient's risk; it does not rewrite their chart."""
    before, _ = plan_demand(cohort(), CANDIDATES)
    after, _ = plan_demand(
        cohort(), CANDIDATES, risk_profiles=[fatal_renal_profile(LISINOPRIL)]
    )
    for rxcui, line in before.items():
        assert after[rxcui].on_therapy == line.on_therapy


def test_at_risk_never_exceeds_on_therapy():
    """`flagged` spans the whole cohort including people who will never take the
    drug. at_risk is a subset of on_therapy; conflating them inflates the
    projection with patients who are not there to leave."""
    lines, _ = plan_demand(
        cohort(), CANDIDATES, risk_profiles=[fatal_renal_profile(LISINOPRIL)]
    )
    for line in lines.values():
        assert line.at_risk <= line.on_therapy, f"{line.rxcui}: at_risk exceeds on_therapy"


# --- switch_in --------------------------------------------------------------


def test_switch_in_lands_on_the_in_class_peer():
    """§2.3: substitution pressure is visible before it happens. The flagged
    lisinopril patient shows as pressure on enalapril, its ace_inhibitor peer."""
    lines, _ = plan_demand(
        cohort(), CANDIDATES, risk_profiles=[fatal_renal_profile(LISINOPRIL)]
    )
    assert lines[ENALAPRIL].switch_in == 1
    assert lines[ATORVASTATIN].switch_in == 0  # different class entirely
    assert lines[LISINOPRIL].switch_in == 0  # never routes to the drug being left


def test_no_pressure_without_anyone_at_risk():
    lines, _ = plan_demand(cohort(), CANDIDATES)
    assert all(line.switch_in == 0 for line in lines.values())


def test_nobody_switches_into_a_drug_that_is_unavailable():
    """COMP-1 withdrew it; it cannot absorb pressure."""
    lines, _ = plan_demand(
        cohort(),
        CANDIDATES,
        unavailable=[ENALAPRIL],
        risk_profiles=[fatal_renal_profile(LISINOPRIL)],
    )
    assert lines[ENALAPRIL].switch_in == 0


def test_a_drug_with_no_in_class_peer_generates_no_pressure():
    """Metformin is the only biguanide in the map, so a flagged patient on it
    has nowhere to go. That is a clinical problem, not a purchasing one."""
    lines, _ = plan_demand(
        cohort(), CANDIDATES, risk_profiles=[fatal_renal_profile(METFORMIN)]
    )
    assert lines[METFORMIN].at_risk == 1
    assert sum(line.switch_in for line in lines.values()) == 0


# --- the arithmetic ---------------------------------------------------------


def test_projection_is_on_therapy_minus_switchers_plus_arrivals():
    line = DemandLine(rxcui=METFORMIN, on_therapy=40, at_risk=30, switch_in=5)
    assert line.projected(0.6) == 27  # 40 - 18 + 5


def test_switch_rate_of_zero_means_nobody_moves():
    line = DemandLine(rxcui=METFORMIN, on_therapy=40, at_risk=30, switch_in=5)
    assert line.projected(0.0) == 45


def test_switch_rate_of_one_moves_every_flagged_patient():
    line = DemandLine(rxcui=METFORMIN, on_therapy=40, at_risk=30, switch_in=5)
    assert line.projected(1.0) == 15


def test_a_projection_cannot_go_negative():
    """An assumption must not be able to produce a negative headcount and have
    it read downstream as a real number of patients."""
    assert DemandLine(rxcui=METFORMIN, on_therapy=2, at_risk=40).projected(0.6) == 0


def test_the_projection_moves_with_the_assumption():
    """Every projected number rests on switch_rate, which is chosen rather than
    measured -- so it must visibly change the answer."""
    lines, _ = plan_demand(
        cohort(), CANDIDATES, risk_profiles=[fatal_renal_profile(LISINOPRIL)]
    )
    line = lines[LISINOPRIL]
    assert line.projected(0.0) == 2
    assert line.projected(1.0) == 1


# --- cohort_fit -------------------------------------------------------------


def test_cohort_fit_is_the_share_who_could_ever_take_it():
    assert DemandLine(rxcui=ATORVASTATIN, eligible=3).cohort_fit(4) == 0.75


def test_cohort_fit_of_an_empty_cohort_is_zero_not_a_crash():
    assert DemandLine(rxcui=ATORVASTATIN, eligible=0).cohort_fit(0) == 0.0


def test_a_drug_nobody_can_take_is_a_bad_bet_however_healthy_usage_looks():
    """§2.3, as arithmetic: high current usage, low cohort fit."""
    line = DemandLine(rxcui=METFORMIN, on_therapy=40, eligible=2)
    assert line.on_therapy == 40
    assert line.cohort_fit(100) == 0.02


@pytest.mark.parametrize("rate", [0.0, 0.25, 0.6, 1.0])
def test_the_same_inputs_always_give_the_same_projection(rate):
    first, _ = plan_demand(cohort(), CANDIDATES)
    again, _ = plan_demand(cohort(), CANDIDATES)
    assert {r: x.projected(rate) for r, x in again.items()} == {
        r: x.projected(rate) for r, x in first.items()
    }
