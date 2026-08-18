"""Findings -> shaded organs.

A shaded organ reads as a clinical claim, so the mapping is pinned rather than
left to whatever the front-end feels like colouring. Two properties matter more
than the individual rows:

* nothing is shaded that the assessment did not raise -- the diagram must not
  say something the ruleset never said;
* nothing is dropped silently -- a finding with no organ is reported, because a
  reader counts organs and believes they have seen everything.
"""

from __future__ import annotations

import pytest
from medstock_shared.organs import (
    FINDING_ORGANS,
    ORGANS,
    compare,
    impacts,
    organs_for_finding,
    organs_for_reaction,
)
from medstock_shared.patient import WEIGHTS, Finding, Severity


def f(code, message="", weight=10, severity=Severity.MODERATE):
    return Finding(code, severity, weight, message, "test", 1)


# --- the table itself ---------------------------------------------------------


def test_every_weighted_finding_has_a_decision():
    """A new finding must not default to invisible. Mapping to () is a decision;
    being absent from the table is an oversight."""
    missing = set(WEIGHTS) - set(FINDING_ORGANS)
    assert not missing, f"no organ decision recorded for: {missing}"


def test_mapped_organs_are_drawable():
    for code, organs in FINDING_ORGANS.items():
        for organ in organs:
            assert organ in ORGANS, f"{code} maps to {organ}, which is not on the diagram"


# --- individual findings ------------------------------------------------------


def test_renal_dosing_shades_the_kidneys():
    assert organs_for_finding("RENAL_DOSE_EXCEEDED") == ("kidneys",)


def test_hepatic_impairment_shades_the_liver():
    assert organs_for_finding("HEPATIC_IMPAIRED") == ("liver",)


def test_an_allergy_shades_where_it_shows_and_where_it_kills():
    assert set(organs_for_finding("ALLERGY_MATCH")) == {"skin", "lungs"}


def test_an_interaction_shades_the_liver_not_the_target_organ():
    """An interaction is about clearance, not about what either drug treats."""
    assert organs_for_finding("INTERACTION_MAJOR") == ("liver",)


def test_a_duplicate_shades_the_organ_its_class_stacks_on():
    assert set(organs_for_finding("DUPLICATE_CLASS", drug_class="nsaid")) == {
        "stomach",
        "kidneys",
    }
    assert organs_for_finding("DUPLICATE_CLASS", drug_class="anticoagulant") == ("blood",)


def test_a_duplicate_of_an_unknown_class_shades_nothing():
    assert organs_for_finding("DUPLICATE_CLASS", drug_class="mystery") == ()


# --- reaction text ------------------------------------------------------------


@pytest.mark.parametrize(
    "reaction,organ",
    [
        ("Lactic acidosis", "kidneys"),
        ("Hepatic failure", "liver"),
        ("QT prolongation", "heart"),
        ("Gastrointestinal haemorrhage", "blood"),
        ("Stevens-Johnson syndrome", "skin"),
        ("Respiratory depression", "lungs"),
        ("Seizure", "brain"),
        ("Severe diarrhoea", "intestines"),
    ],
)
def test_a_reaction_name_finds_its_organ(reaction, organ):
    assert organ in organs_for_reaction(reaction)


def test_hepatic_failure_is_a_liver_not_a_heart():
    """Ordering in the table is load-bearing: 'failure' must not reach the heart
    before 'hepat' reaches the liver."""
    assert organs_for_reaction("hepatic failure") == ("liver",)


def test_an_unrecognised_reaction_shades_nothing():
    assert organs_for_reaction("general malaise") == ()


def test_only_population_signals_read_the_message():
    """Reading prose for every code would let a phrase in a sentence shade an
    organ the ruleset never implicated."""
    assert organs_for_finding("NARROW_THERAPEUTIC_INDEX", "hepatic failure risk") == ()
    assert organs_for_finding("ADR_SIGNAL", "hepatic failure") == ("liver",)


# --- aggregation --------------------------------------------------------------


def test_findings_on_one_organ_are_merged_worst_first():
    got, _ = impacts([
        f("RENAL_DOSE_EXCEEDED", "renal", 30, Severity.HIGH),
        f("ADR_SIGNAL", "Lactic acidosis", 5, Severity.LOW),
    ])
    kidneys = next(i for i in got if i.organ == "kidneys")
    assert kidneys.severity == "high", "a kidney carrying a high and a low is high"
    assert kidneys.weight == 35, "weights sum, so the diagram can rank organs"


def test_the_heaviest_organ_is_listed_first():
    got, _ = impacts([
        f("ADR_SIGNAL", "nausea", 5, Severity.LOW),
        f("HEPATIC_IMPAIRED", "liver", 20, Severity.HIGH),
    ])
    assert got[0].organ == "liver"


def test_an_unmappable_finding_is_reported_not_dropped():
    """The property that keeps the picture honest."""
    got, unmapped = impacts([f("NARROW_THERAPEUTIC_INDEX", "narrow index", 10)])
    assert got == []
    assert unmapped == ["NARROW_THERAPEUTIC_INDEX"]


def test_reasons_are_carried_for_the_tooltip():
    got, _ = impacts([f("HEPATIC_IMPAIRED", "Hepatic impairment, dose reduction advised", 20)])
    assert "dose reduction" in got[0].reasons[0]


def test_nothing_is_shaded_without_a_finding():
    assert impacts([]) == ([], [])


# --- the analogue comparison --------------------------------------------------


def test_a_substitute_that_moves_the_problem():
    current = [f("RENAL_DOSE_EXCEEDED", "renal", 30, Severity.HIGH)]
    candidate = [f("HEPATIC_IMPAIRED", "hepatic", 20, Severity.HIGH)]
    got = compare(current, candidate)
    assert got["relieved"] == ["kidneys"]
    assert got["introduced"] == ["liver"]


def test_a_substitute_that_changes_nothing_says_so():
    """The honest answer a substitution often has. A view that only showed
    'relieved' would make every swap look like an improvement."""
    same = [f("RENAL_DOSE_EXCEEDED", "renal", 30, Severity.HIGH)]
    got = compare(same, list(same))
    assert got["unchanged"] == ["kidneys"]
    assert got["relieved"] == [] and got["introduced"] == []


def test_the_comparison_surfaces_unmapped_from_both_sides():
    got = compare(
        [f("NARROW_THERAPEUTIC_INDEX", "", 10)],
        [f("PRIOR_ADR_SAME_CLASS", "", 45)],
    )
    assert set(got["unmapped"]) == {"NARROW_THERAPEUTIC_INDEX", "PRIOR_ADR_SAME_CLASS"}
