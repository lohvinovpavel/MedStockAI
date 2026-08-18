"""One reaction, several places in the label.

A label routinely states the same risk more than once — metformin's lactic
acidosis appears in the boxed warning and twice more in warnings and cautions,
which is what the live extraction returns. Both naive handlings are wrong, and
this pins the middle path:

* three rows would make the matcher score one reaction three times;
* letting the (rxcui, reaction) key collapse them discards two at random, and
  the boxed warning is as likely to be dropped as kept.
"""

from __future__ import annotations

from app.prognosis import merge_by_reaction


def row(**kw) -> dict:
    base = {
        "rxcui": "861007",
        "reaction": "lactic acidosis",
        "seriousness": "fatal",
        "risk_factors": [{"feature": "egfr_band", "op": "at_or_below", "value": "45-59"}],
        "citation": "from warnings",
        "section": "warnings_and_cautions",
        "spl_id": "spl-1",
        "status": "awaiting_approval",
    }
    return {**base, **kw}


def test_one_reaction_becomes_one_row():
    merged = merge_by_reaction([row(), row(section="boxed_warning"), row()])
    assert len(merged) == 1


def test_factors_from_every_section_are_kept():
    """The whole point: nothing the label said about this reaction is lost."""
    merged = merge_by_reaction(
        [
            row(risk_factors=[{"feature": "egfr_band", "op": "at_or_below", "value": "45-59"}]),
            row(
                section="boxed_warning",
                risk_factors=[{"feature": "age_band", "op": "in", "value": ["75-89"]}],
            ),
            row(risk_factors=[{"feature": "hepatic", "op": "eq", "value": "impaired"}]),
        ]
    )
    features = {f["feature"] for f in merged[0]["risk_factors"]}
    assert features == {"egfr_band", "age_band", "hepatic"}


def test_an_identical_factor_is_not_duplicated():
    """Restating the same condition must not inflate the denominator — the
    matcher scores matched/total, so a duplicated factor would quietly lower
    every score for this reaction."""
    merged = merge_by_reaction([row(), row(section="boxed_warning"), row()])
    assert len(merged[0]["risk_factors"]) == 1


def test_the_boxed_warning_supplies_the_quote():
    """FDA puts in a boxed warning what it most wants read."""
    merged = merge_by_reaction(
        [
            row(citation="from warnings", section="warnings_and_cautions"),
            row(citation="from the box", section="boxed_warning"),
        ]
    )
    assert merged[0]["citation"] == "from the box"
    assert merged[0]["section"] == "boxed_warning"


def test_section_preference_does_not_depend_on_order():
    forward = merge_by_reaction(
        [row(citation="box", section="boxed_warning"), row(citation="warn")]
    )
    reverse = merge_by_reaction(
        [row(citation="warn"), row(citation="box", section="boxed_warning")]
    )
    assert forward[0]["citation"] == reverse[0]["citation"] == "box"


def test_the_gravest_seriousness_wins():
    """Two readings of one reaction disagreeing on severity resolve upward. A
    fatal reaction recorded as moderate is the failure that matters."""
    merged = merge_by_reaction(
        [row(seriousness="moderate"), row(seriousness="fatal", section="boxed_warning")]
    )
    assert merged[0]["seriousness"] == "fatal"


def test_different_reactions_stay_separate():
    merged = merge_by_reaction([row(), row(reaction="vitamin B12 deficiency")])
    assert len(merged) == 2


def test_reaction_matching_is_case_insensitive():
    merged = merge_by_reaction([row(reaction="Lactic Acidosis"), row(reaction="lactic acidosis")])
    assert len(merged) == 1


def test_nothing_in_becomes_nothing_out():
    assert merge_by_reaction([]) == []
