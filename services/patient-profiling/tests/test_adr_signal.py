"""Tier 1 — FAERS disproportionality as a population signal.

docs/patient-profiling-usecases.md §3 Tier 1, stage 7 of docs/patient-pipeline.md.

The thing most worth pinning here is not the arithmetic, it is the framing. A
proportional reporting ratio is a statement about *reports*, and the one way this
tier does harm is by being read as a statement about risk. So there are tests on
the wording, not only on the numbers.
"""

from __future__ import annotations

from medstock_shared.patient import (
    WEIGHTS,
    AdrSignalRow,
    PatientVector,
    Verdict,
    adr_findings,
    assess,
)

METFORMIN = "861007"
LISINOPRIL = "29046"

HEALTHY = PatientVector(age_band="40-64", egfr_band=">=90", hepatic="normal")


def signal(**kw) -> AdrSignalRow:
    base = {
        "rxcui": METFORMIN,
        "reaction": "LACTIC ACIDOSIS",
        "prr": 6.0,
        "ror": 6.5,
        "n_reports": 120,
    }
    return AdrSignalRow(**{**base, **kw})


def codes(findings) -> set[str]:
    return {f.code for f in findings}


# --- the reporting floor -----------------------------------------------------


def test_a_ratio_over_too_few_reports_is_discarded():
    """2 reports out of 3 gives a spectacular PRR that the next report
    destroys. The count floor matters more than the ratio."""
    assert adr_findings([signal(n_reports=2, prr=40.0)]) == []


def test_a_ratio_at_baseline_is_not_a_signal():
    assert adr_findings([signal(prr=1.0)]) == []
    assert adr_findings([signal(prr=1.9)]) == []


def test_the_signal_threshold_is_inclusive():
    assert len(adr_findings([signal(prr=2.0)])) == 1


# --- strength ----------------------------------------------------------------


def test_a_strong_signal_weighs_more_than_a_weak_one():
    weak = adr_findings([signal(prr=2.5)])[0]
    strong = adr_findings([signal(prr=9.0)])[0]
    assert weak.code == "ADR_SIGNAL"
    assert strong.code == "ADR_SIGNAL_STRONG"
    assert strong.weight > weak.weight


def test_tier_one_stays_smaller_than_the_stages_that_know_the_patient():
    """A FAERS ratio is identical for every patient on the drug. If it could
    outweigh a renal or prior-ADR finding it would flatten the very thing the
    assessment exists to distinguish."""
    assert WEIGHTS["ADR_SIGNAL_STRONG"] < WEIGHTS["RENAL_DOSE_EXCEEDED"]
    assert WEIGHTS["ADR_SIGNAL_STRONG"] < WEIGHTS["PRIOR_ADR_SAME_CLASS"]


# --- the wording is part of the contract -------------------------------------


def test_the_message_says_reported_and_not_caused():
    message = adr_findings([signal()])[0].message
    assert "reported" in message.lower()
    for forbidden in ("causes", "caused", "will cause"):
        assert forbidden not in message.lower()


def test_the_message_carries_the_count_and_the_caveat():
    """A ratio without its report count cannot be judged, and a ratio without
    the caveat reads as a risk."""
    message = adr_findings([signal(n_reports=120)])[0].message
    assert "120" in message
    assert "no denominator" in message.lower() or "not a risk" in message.lower()


def test_the_source_names_faers():
    assert adr_findings([signal()])[0].source == "openFDA FAERS"


# --- wiring ------------------------------------------------------------------


def test_stage_seven_runs_and_another_drugs_signal_does_not_leak():
    with_signal = assess(HEALTHY, METFORMIN, adr_signals=[signal()])
    assert 7 in with_signal.stages_completed
    assert "ADR_SIGNAL_STRONG" in codes(with_signal.findings)

    other = assess(HEALTHY, LISINOPRIL, adr_signals=[signal()])
    assert 7 not in other.stages_completed


def test_stage_seven_is_listed_once_when_both_halves_run():
    """7a (FAERS) and 7b (label prognosis) share the stage number.
    `stages_completed` records stages that ran, not findings produced."""
    from medstock_shared.patient import RiskProfile

    profile = RiskProfile(
        rxcui=METFORMIN,
        reaction="lactic acidosis",
        seriousness="fatal",
        risk_factors=({"feature": "egfr_band", "op": "at_or_below", "value": ">=90"},),
        citation="boxed warning",
    )
    result = assess(HEALTHY, METFORMIN, risk_profiles=[profile], adr_signals=[signal()])
    assert result.stages_completed.count(7) == 1


def test_tier_one_never_blocks():
    result = assess(HEALTHY, METFORMIN, adr_signals=[signal(prr=99.0, n_reports=9999)])
    assert result.verdict is not Verdict.BLOCKED
