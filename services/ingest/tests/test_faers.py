"""The FAERS 2x2 arithmetic behind Tier 1.

docs/patient-profiling-usecases.md §3 Tier 1. The measures are standard, so what
is worth pinning is the handling of the cases where they are *not* defined --
openFDA's 1 000-term ceiling leaving a reaction with no baseline, and an empty
cell that a continuity correction would paper over.
"""

from __future__ import annotations

import pytest
from app.faers import signals_for

METFORMIN = "861007"


class _Fetcher:
    """Stands in for openFDA. Two reactions for the drug against a known
    background, so PRR and ROR can be checked against hand arithmetic."""

    def __init__(self):
        self.calls = 0

    def __call__(self, url, params=None):
        self.calls += 1
        params = params or {}
        if "search" in params and "count" in params:
            return {
                "results": [
                    {"term": "LACTIC ACIDOSIS", "count": 100},
                    {"term": "HEADACHE", "count": 100},
                ]
            }
        raise AssertionError("unexpected call")


def test_prr_and_ror_match_the_two_by_two_table(monkeypatch):
    """a=100, a+b=200, a+c=1000, N=100000.
    b = 100, c = 900, d = N - (a+b) - c = 98900.
    PRR = (100/200) / (900/99800) = 0.5 / 0.009018 = 55.44
    ROR = (100*98900) / (100*900) = 109.89
    """
    monkeypatch.setattr("app.faers.fetch_json", _Fetcher())
    rows = signals_for(METFORMIN, {"LACTIC ACIDOSIS": 1000, "HEADACHE": 50000}, 100_000)
    by_reaction = {r["reaction"]: r for r in rows}

    la = by_reaction["LACTIC ACIDOSIS"]
    assert la["prr"] == pytest.approx(55.44, abs=0.05)
    assert la["ror"] == pytest.approx(109.89, abs=0.05)
    assert la["n_reports"] == 100
    assert la["n_drug_reports"] == 200


def test_a_reaction_with_no_baseline_is_skipped_not_guessed(monkeypatch):
    """openFDA's count endpoint tops out at 1 000 terms, so a rare reaction may
    have no background. Inventing one would manufacture a signal."""
    monkeypatch.setattr("app.faers.fetch_json", _Fetcher())
    rows = signals_for(METFORMIN, {"LACTIC ACIDOSIS": 1000}, 100_000)
    assert {r["reaction"] for r in rows} == {"LACTIC ACIDOSIS"}


def test_an_empty_cell_is_skipped_rather_than_corrected(monkeypatch):
    """If every report of the reaction names this drug, c is zero and both
    measures are undefined. A continuity correction here would invent precision
    the data does not have."""
    monkeypatch.setattr("app.faers.fetch_json", _Fetcher())
    rows = signals_for(METFORMIN, {"LACTIC ACIDOSIS": 100, "HEADACHE": 50000}, 100_000)
    assert "LACTIC ACIDOSIS" not in {r["reaction"] for r in rows}
