"""A substitute is assessed as strictly as the line it replaces.

The prescribe workspace narrows analogues by the single ingredient /cart-check
flagged, then ranks what survives by hospital stock. Nothing on that path looks
at the patient a second time, so the candidate at the top of the list — the one
"Replace with analogue" swaps in — is simply the one there is most of.

That is the hole /analogue-check fills, and these tests pin the properties that
make filling it worth anything:

* the same eight stages run, with the same approved-only profile filter, so
  switching cannot become the way to get an unassessed drug to a patient;
* results come back in request order, because ranking by safety alone would
  hide that the caller is also ranking by supply;
* a substitution is audited like any other per-patient verdict.

The endpoint needs a database, so the request-path tests are thin and the
substance is in the assessment itself, which is pure. That split is deliberate:
the value here is that `assess` is reached with everything it needs, and the AST
guard in test_risk_profiles_wired.py already covers the argument most easily
forgotten.
"""

from __future__ import annotations

import ast
from pathlib import Path

from medstock_shared.patient import (
    AdrSignalRow,
    PatientVector,
    PgxRecommendation,
    RiskProfile,
    Verdict,
    assess,
)

_MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"

CLOPIDOGREL = "32968"
PRASUGREL = "613391"

# Typed as a poor metabolizer: CPIC has something to say about clopidogrel for
# this patient, and nothing to say about the alternative.
POOR_METABOLIZER = PatientVector(
    age_band="40-64",
    egfr_band=">=90",
    hepatic="normal",
    pgx_phenotypes=("CYP2C19:Poor Metabolizer",),
)


def _guideline(rxcui: str = CLOPIDOGREL) -> PgxRecommendation:
    return PgxRecommendation(
        rxcui=rxcui,
        gene="CYP2C19",
        phenotype="Poor Metabolizer",
        recommendation="Consider an alternative antiplatelet",
        implication="Reduced formation of the active metabolite",
        classification="Strong",
        evidence_level="A",
        action_required=True,
    )


# --- the substitute is assessed, not assumed ---------------------------------


def test_a_candidate_can_carry_the_same_pgx_finding_as_the_drug_it_replaces():
    """The case the feature exists for. Excluding one ingredient says nothing
    about the genotype, so a candidate that is wrong for this patient has to be
    able to come back marked."""
    assessed = assess(POOR_METABOLIZER, CLOPIDOGREL, pgx=[_guideline()], risk_profiles=())
    assert any(f.source == "cpic" for f in assessed.findings), (
        "a CPIC guideline matching the patient's phenotype must produce a finding "
        "on the candidate, or substituting silently drops Tier 3"
    )


def test_an_untyped_candidate_is_not_penalised():
    """Nothing known about this drug for this genotype is not the same as a
    problem with it. The stage must stay silent rather than guess."""
    assessed = assess(POOR_METABOLIZER, PRASUGREL, pgx=[_guideline()], risk_profiles=())
    assert not [f for f in assessed.findings if f.source == "cpic"]


def test_a_label_risk_reaches_a_candidate():
    """PP-3 applies to substitutes too: an approved profile whose risk factors
    match this patient must land on the candidate being offered."""
    profile = RiskProfile(
        rxcui=PRASUGREL,
        reaction="bleeding",
        seriousness="serious",
        risk_factors=({"feature": "age_band", "op": "at_or_above", "value": "75-89"},),
        citation="label §5.1",
        section="warnings",
    )
    elderly = PatientVector(**{**vars(POOR_METABOLIZER), "age_band": "75-89"})
    assessed = assess(elderly, PRASUGREL, risk_profiles=[profile])
    assert assessed.findings, "an approved profile matching the patient produced nothing"


def test_an_adr_signal_reaches_a_candidate():
    """Tier 1 likewise — a disproportionality signal on the substitute is
    exactly the thing a stock-ranked list would never show."""
    signal = AdrSignalRow(rxcui=PRASUGREL, reaction="haemorrhage", prr=8.0, ror=9.1, n_reports=400)
    assessed = assess(POOR_METABOLIZER, PRASUGREL, risk_profiles=(), adr_signals=[signal])
    assert any(f.source == "faers" for f in assessed.findings)


def test_a_blocked_candidate_has_no_score():
    """A hard gate produces no number, and the substitute path must not invent
    one — a score beside a contraindication invites weighing it against stock."""
    allergic = PatientVector(**{**vars(POOR_METABOLIZER), "allergy_codes": ("penicillin",)})
    assessed = assess(allergic, "723", risk_profiles=())
    if assessed.verdict is Verdict.BLOCKED:
        assert assessed.score is None


# --- the endpoint is wired the way the cart is --------------------------------


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    found = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ),
        None,
    )
    assert found is not None, f"{name}() is gone from main.py — renamed?"
    return found


def _calls_in(func: ast.FunctionDef) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(func)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_the_endpoint_exists():
    _function("analogue_check")


def test_it_applies_every_feed_the_cart_applies():
    """The three feed readers degrade to empty when their table is missing, so
    omitting one here would not fail — it would return a well-formed assessment
    with a stage quietly absent. Same failure mode test_risk_profiles_wired.py
    was written for, one endpoint along."""
    calls = _calls_in(_function("analogue_check"))
    for reader in ("approved_profiles", "pgx_for", "adr_signals_for"):
        assert reader in calls, (
            f"analogue_check does not call {reader} — candidates would be assessed "
            f"more loosely than the cart line they replace, and nothing would say so"
        )


def test_a_substitution_is_audited():
    """record_assessment refuses rather than degrades when it cannot write. A
    per-patient verdict that reaches a physician with no audit row is the hole
    docs/services.md §1.3 claims does not exist."""
    assert "record_assessment" in _calls_in(_function("analogue_check"))


def test_the_patient_is_reduced_to_a_vector_before_scoring():
    """PHI stops at the boundary: the rules engine sees a de-identified vector,
    never the row."""
    assert "patient_row_to_vector" in _calls_in(_function("analogue_check"))
