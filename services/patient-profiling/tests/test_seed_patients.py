"""The demo seed: a cohort worth having, and a tenant that actually exists.

Two things were wrong with this script and both were silent.

The first is scale. A demo environment held two patients, so `/demand`, the
PP-4 forecast and every population panel answered questions about a cohort of
two and looked broken rather than empty.

The second is worse. `patient.hospital_id` is Text with **no foreign key**, and
the script defaulted to the literal `00000000-0000-0000-0000-000000000001`.
Nothing creates a hospital with that id — `services/auth/app/seed.py` lets
Postgres generate one — so the seed wrote rows, reported success, and every
user saw an empty picker, because a user only ever sees the hospital in their
token. A seed that cannot fail is not the same as a seed that works.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from seed_patients import (
    DEFAULT_HOSPITAL_NAME,
    DEMO_PATIENTS,
    generated_patients,
    resolve_hospital_id,
)

# --- the cohort -------------------------------------------------------------


def test_the_same_seed_produces_the_same_people():
    """A rebuilt environment has to be comparable to the one before it. If the
    cohort reshuffles on every run, no forecast can be compared across a
    redeploy and nobody can tell a model change from a data change."""
    assert generated_patients(50) == generated_patients(50)


def test_a_different_seed_produces_different_people():
    assert generated_patients(50) != generated_patients(50, seed=1)


def test_the_natural_key_is_unique_across_a_thousand():
    """(full_name, date_of_birth) is what the insert de-duplicates on. Forty
    first names by thirty surnames is 1 200 combinations, so a 1 000-person
    cohort drawn without the index suffix would collide constantly -- birthday
    problem, not bad luck -- and the second run would skip most of the rows."""
    people = generated_patients(1000)
    keys = {(p["full_name"], p["date_of_birth"]) for p in people}
    assert len(keys) == 1000


def test_most_patients_have_no_genotype_on_file():
    """Tier 3 is only impressive if the cohort is honest about how rarely a
    hospital has genotyped anyone. A fully genotyped cohort would make the
    pharmacogenomic tier look far more useful than it is before a hospital
    invests in testing."""
    people = generated_patients(1000)
    genotyped = sum(1 for p in people if p["pgx_phenotypes"])
    assert 0.3 < genotyped / len(people) < 0.6


def test_the_cohort_still_covers_every_phenotype_the_rules_can_match():
    """The counterpart to the test above: a weights change that quietly drove
    every phenotype to None would leave Tier 3 built, correct and invisible --
    which is exactly how it shipped the first time."""
    people = generated_patients(1000)
    genes = {p["pgx_phenotypes"][0].split(":")[0] for p in people if p["pgx_phenotypes"]}
    assert {"CYP2C19", "CYP2D6", "G6PD"} <= genes


def test_the_curated_patients_are_not_generated():
    """The eight hand-written stories are the demo script. They must keep their
    exact names, because a walkthrough and the docs both name them."""
    curated = {p["full_name"] for p in DEMO_PATIENTS}
    assert "Elena Vasquez" in curated
    assert not any("#" in name for name in curated)


def test_ages_skew_old():
    """A formulary is consumed mostly by older patients. An age-flat cohort
    would make the PP-4 forecast -- which turns on how many patients age out of
    a therapy -- look far calmer than reality."""
    years = sorted(p["date_of_birth"].year for p in generated_patients(1000))
    assert years[len(years) // 2] < 1975


# --- the tenant -------------------------------------------------------------


class _FakeSession:
    """Just enough of a Session to answer one `select(Hospital)`."""

    def __init__(self, row):
        self._row = row

    def execute(self, _stmt):
        return self

    def scalars(self):
        return self

    def first(self):
        return self._row


class _Hospital:
    def __init__(self, id_):
        self.id = id_


def test_an_explicit_hospital_id_wins():
    """Tests and one-off environments need to name a tenant that has no
    `hospital` row at all, so an explicit id must not be second-guessed."""
    session = _FakeSession(_Hospital("should-not-be-used"))
    assert resolve_hospital_id(session, "11111111-1111-1111-1111-111111111111", "ignored") == (
        "11111111-1111-1111-1111-111111111111"
    )


def test_the_tenant_is_resolved_by_name_when_no_id_is_given():
    session = _FakeSession(_Hospital("1a1652c6-3338-4657-a9c0-a4be59be71d7"))
    assert resolve_hospital_id(session, None, DEFAULT_HOSPITAL_NAME) == (
        "1a1652c6-3338-4657-a9c0-a4be59be71d7"
    )


def test_a_missing_hospital_stops_the_seed_instead_of_inventing_one():
    """The regression this whole module exists for. Falling back to a constant
    put a thousand invisible rows in the table and printed 'seeded 1008' --
    strictly worse than a job that fails and says why."""
    with pytest.raises(SystemExit) as exc:
        resolve_hospital_id(_FakeSession(None), None, "No Such Hospital")
    assert "No Such Hospital" in str(exc.value)
    assert "--hospital-id" in str(exc.value)
