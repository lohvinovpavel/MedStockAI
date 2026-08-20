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


def test_a_duplicate_shades_the_organs_its_class_stacks_on():
    """A doubled NSAID stacks on three, not two. The oesophagus was added when
    the organ set widened: reflux and oesophageal ulceration are where a second
    NSAID shows up before the stomach does, and leaving it out sent those
    findings to the unmapped list."""
    assert set(organs_for_finding("DUPLICATE_CLASS", drug_class="nsaid")) == {
        "stomach",
        "kidneys",
        "oesophagus",
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


def test_an_avoided_ingredient_shades_the_ingredients_organ():
    """The advisory names an ingredient, not a reaction, and caffeine is a
    stimulant: it must reach the heart and the brain, not draw nothing. This is
    the 'avoid caffeine does not work' gap -- the finding badged the line but
    left the figure blank because it was in no organ table."""
    message = "Patient profile flags 'caffeine' — this drug contains caffeine"
    assert organs_for_finding("AVOIDED_INGREDIENT", message) == ("heart", "brain")


def test_an_avoided_ingredient_with_no_organ_entry_is_reported_not_guessed():
    """An ingredient with no line in INGREDIENT_ORGANS is unmapped, not shaded
    somewhere plausible -- the same honesty the reaction table keeps."""
    got, unmapped = impacts(
        [f("AVOIDED_INGREDIENT", "this drug contains sorbitol", 0, Severity.LOW)]
    )
    assert got == []
    assert unmapped == ["AVOIDED_INGREDIENT"]


def test_an_avoided_caffeine_finding_shades_through_impacts():
    """End to end through impacts(), the shape the cart figure consumes."""
    got, unmapped = impacts(
        [f("AVOIDED_INGREDIENT", "this drug contains caffeine", 0, Severity.LOW)]
    )
    assert {i.organ for i in got} == {"heart", "brain"}
    assert unmapped == []


# --- the model-backed organ fallback -----------------------------------------


def test_the_llm_places_a_reaction_the_tables_miss():
    """A rare reaction word REACTION_ORGANS has no line for is sent to the
    fallback, and a valid organ comes back shaded rather than unmapped."""
    got, unmapped = impacts(
        [f("ADR_SIGNAL", "toxic epidermal necrolysis", 8, Severity.HIGH)],
        infer_organs=lambda _msg: ("skin",),
    )
    assert {i.organ for i in got} == {"skin"}
    assert unmapped == []


def test_the_llm_is_only_asked_for_prose_carrying_codes():
    """A code deliberately mapped to 'no single organ' is a design decision, not
    a gap: the fallback must never be consulted for it, or the figure would
    guess an organ the ruleset refused to name."""
    calls: list[str] = []

    def spy(msg):
        calls.append(msg)
        return ("liver",)

    got, unmapped = impacts(
        [f("NARROW_THERAPEUTIC_INDEX", "narrow index", 10)], infer_organs=spy
    )
    assert got == []
    assert unmapped == ["NARROW_THERAPEUTIC_INDEX"]
    assert calls == []  # never sent to the model


def test_the_llm_is_not_asked_when_the_tables_already_placed_it():
    """The deterministic path wins: a reaction the substring table catches must
    not spend a model call."""
    calls: list[str] = []

    def spy(msg):
        calls.append(msg)
        return ("brain",)

    got, _ = impacts([f("ADR_SIGNAL", "hepatic failure", 8)], infer_organs=spy)
    assert {i.organ for i in got} == {"liver"}  # from REACTION_ORGANS, not the spy
    assert calls == []


def test_an_empty_llm_answer_leaves_the_finding_unmapped():
    """The fallback declining is the same as before it existed: reported, not
    shaded somewhere plausible."""
    got, unmapped = impacts(
        [f("ADR_SIGNAL", "some unplaceable effect", 8)], infer_organs=lambda _m: ()
    )
    assert got == []
    assert unmapped == ["ADR_SIGNAL"]


def test_an_off_list_organ_from_the_llm_is_dropped():
    """impacts() filters the fallback to drawable organs, so a body part the
    figure has no anchor for cannot be shaded even if the model returns it."""
    got, unmapped = impacts(
        [f("ADR_SIGNAL", "odd effect", 8)],
        infer_organs=lambda _m: ("pineal_gland", "liver"),
    )
    assert {i.organ for i in got} == {"liver"}
    assert unmapped == []


def test_without_a_resolver_behaviour_is_unchanged():
    """The fallback is opt-in: no resolver, and an unplaceable reaction is
    unmapped exactly as it always was."""
    got, unmapped = impacts([f("ADR_SIGNAL", "some unplaceable effect", 8)])
    assert got == []
    assert unmapped == ["ADR_SIGNAL"]


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


def test_condition_worsened_has_an_organ_decision():
    """It maps to nothing on purpose -- which organ depends on the condition and
    class pair, and that pair lives in the message rather than a structured
    field. Recorded as a decision so the reachability guard stays honest."""
    assert FINDING_ORGANS.get("CONDITION_WORSENED", "missing") == ()
