"""Tier 3 — pharmacogenomics as a guideline lookup.

docs/patient-profiling-usecases.md §3. Tier 3 is the tier that is supposed to be
trivially explainable: the patient's reported phenotype either matches a CPIC row
for this drug or it does not. These tests pin that it stays a lookup — that it
never blocks, never fires on a gene the patient was not typed for, and never
scores a genotype that CPIC considers ordinary.

The phenotype strings are the real vocabulary loaded from CPIC (Normal /
Intermediate / Poor / Ultrarapid Metabolizer, Indeterminate), not invented ones.
"""

from __future__ import annotations

from medstock_shared.patient import (
    PatientVector,
    PgxRecommendation,
    Verdict,
    assess,
    is_baseline_phenotype,
    pgx_findings,
)

CLOPIDOGREL = "32968"
CITALOPRAM = "2556"

TYPED = PatientVector(
    age_band="40-64",
    egfr_band=">=90",
    hepatic="normal",
    pgx_phenotypes=("CYP2C19:Poor Metabolizer",),
)


def rec(**kw) -> PgxRecommendation:
    base = {
        "rxcui": CLOPIDOGREL,
        "gene": "CYP2C19",
        "phenotype": "Poor Metabolizer",
        "recommendation": "Consider an alternative antiplatelet",
        "implication": "Reduced formation of the active metabolite",
        "classification": "Strong",
        "evidence_level": "A",
        "action_required": True,
    }
    return PgxRecommendation(**{**base, **kw})


def codes(findings) -> set[str]:
    return {f.code for f in findings}


# --- it only fires on what the patient was actually typed for ----------------


def test_no_genotype_means_no_findings():
    """The ordinary case: nobody has sent a phenotype, so Tier 3 has nothing to
    say. It must say nothing rather than guess."""
    untyped = PatientVector(**{**vars(TYPED), "pgx_phenotypes": ()})
    assert pgx_findings(untyped, [rec()]) == []


def test_a_matching_phenotype_produces_a_weighted_finding():
    findings = pgx_findings(TYPED, [rec()])
    assert codes(findings) == {"PGX_ACTIONABLE"}
    assert findings[0].weight == 40  # Strong
    assert "Consider an alternative antiplatelet" in findings[0].message


def test_a_different_phenotype_of_the_same_gene_does_not_match():
    """A poor metaboliser is not an ultrarapid one."""
    assert pgx_findings(TYPED, [rec(phenotype="Ultrarapid Metabolizer")]) == []


def test_the_gene_has_to_match_too():
    """Same phenotype name, different gene. CYP2D6 poor metabolism says nothing
    about a CYP2C19-mediated drug, and matching on the phenotype word alone
    would fire on every gene the patient was never typed for."""
    assert pgx_findings(TYPED, [rec(gene="CYP2D6")]) == []


def test_matching_is_case_insensitive():
    shouty = PatientVector(**{**vars(TYPED), "pgx_phenotypes": ("cyp2c19:POOR METABOLIZER",)})
    assert len(pgx_findings(shouty, [rec()])) == 1


def test_a_phenotype_with_no_gene_prefix_never_matches():
    """Guards the "GENE:phenotype" contract. A bare "Poor Metabolizer" is not
    attributable to a gene and must not be treated as if it were."""
    bare = PatientVector(**{**vars(TYPED), "pgx_phenotypes": ("Poor Metabolizer",)})
    assert pgx_findings(bare, [rec()]) == []


# --- weighting ---------------------------------------------------------------


def test_weight_follows_cpic_classification():
    assert pgx_findings(TYPED, [rec(classification="Strong")])[0].weight == 40
    assert pgx_findings(TYPED, [rec(classification="Moderate")])[0].weight == 25
    assert pgx_findings(TYPED, [rec(classification="Optional")])[0].weight == 10


def test_an_unrecognised_classification_does_not_score_zero():
    """Falling to zero would silently drop a real guideline out of the score.
    Unknown strength is treated as moderate, which is visible in the finding."""
    assert pgx_findings(TYPED, [rec(classification="No Recommendation")])[0].weight == 25


def test_a_reassuring_genotype_is_reported_but_not_scored():
    """"We checked and your genotype is the ordinary one" is worth saying --
    silence is indistinguishable from never having looked -- but it is not a
    risk and must not move the score."""
    normal = PatientVector(**{**vars(TYPED), "pgx_phenotypes": ("CYP2C19:Normal Metabolizer",)})
    findings = pgx_findings(normal, [rec(phenotype="Normal Metabolizer", action_required=False)])
    assert codes(findings) == {"PGX_STANDARD_DOSING"}
    assert findings[0].weight == 0


# --- the baseline vocabulary (ours, not CPIC's) ------------------------------


def test_baseline_and_uninformative_phenotypes_are_recognised():
    for value in (
        "Normal Metabolizer",
        "Normal Function",
        "normal risk of aminoglycoside-induced hearing loss",
        "*57:01 negative",
        "Indeterminate",
        "uncertain risk of aminoglycoside-induced hearing loss",
        "n/a",
    ):
        assert is_baseline_phenotype(value), value


def test_variant_phenotypes_are_not_baseline():
    for value in (
        "Poor Metabolizer",
        "Intermediate Metabolizer",
        "Ultrarapid Metabolizer",
        "Likely Poor Metabolizer",
        "*57:01 positive",
        "increased risk of aminoglycoside-induced hearing loss",
    ):
        assert not is_baseline_phenotype(value), value


# --- it stays a lookup, wired into assess() ----------------------------------


def test_another_drugs_guideline_does_not_leak_into_this_answer():
    result = assess(TYPED, CLOPIDOGREL, pgx=[rec(rxcui=CITALOPRAM)])
    assert "PGX_ACTIONABLE" not in {f.code for f in result.findings}
    assert 8 not in result.stages_completed


def test_stage_eight_runs_and_is_recorded():
    result = assess(TYPED, CLOPIDOGREL, pgx=[rec()])
    assert 8 in result.stages_completed
    assert "PGX_ACTIONABLE" in {f.code for f in result.findings}


def test_tier_three_never_blocks():
    """§1.5's rule, and it matters most here: abacavir with HLA-B*57:01 is a
    genuine absolute contraindication, and the only way to derive a block from
    this data would be to match words like "avoid" in CPIC's prose. Hard gates
    stay in Tier 0 where they are curated by a person."""
    result = assess(
        TYPED,
        CLOPIDOGREL,
        pgx=[rec(classification="Strong", recommendation="Avoid clopidogrel")],
    )
    assert result.verdict is not Verdict.BLOCKED
    assert result.score is not None


def test_the_genotype_moves_the_verdict():
    """The whole point: a poor metaboliser and a normal one get different
    answers for the same drug."""
    normal = PatientVector(**{**vars(TYPED), "pgx_phenotypes": ("CYP2C19:Normal Metabolizer",)})
    typed_result = assess(TYPED, CLOPIDOGREL, pgx=[rec()])
    normal_result = assess(
        normal, CLOPIDOGREL, pgx=[rec(phenotype="Normal Metabolizer", action_required=False)]
    )
    assert typed_result.score > normal_result.score
