"""PP-3 in the use-cases numbering: "why did it say that?"

docs/patient-profiling-usecases.md §0 and §6. Criterion (d) of the FDA CDS
exclusion requires that a professional can independently review the *basis* of a
recommendation, and FDA's 2022 guidance reads it strictly: a bare risk score with
no reviewable basis does not qualify. So the properties worth pinning are that
the arithmetic adds up, and that an old decision is never quietly re-explained
with today's weights.
"""

from __future__ import annotations

import pytest
from app.main import _band_for, app
from fastapi.testclient import TestClient
from medstock_shared.auth import PERMS, Principal, current_principal
from medstock_shared.patient import BANDS, RULESET_VERSION

PHARMACIST = Principal("pharm-1", "hospital-1", "pharmacist")
PHYSICIAN = Principal("phys-1", "hospital-1", "physician")
DIRECTOR = Principal("dir-1", "hospital-1", "director")


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def as_role(principal: Principal) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: principal
    return TestClient(app)


# --- who may ask why ---------------------------------------------------------


def test_the_prescriber_may_ask_why():
    """Withholding this from the physician would undermine the CDS exclusion
    the design leans on: a prescriber handed a verdict with no way to review its
    basis is the exact case criterion (d) excludes."""
    assert "profile:explain" in PERMS["physician"]
    assert "profile:explain" in PERMS["pharmacist"]


def test_a_director_cannot_read_a_clinical_explanation():
    """A purchasing role has no business reading per-assessment clinical
    reasoning; the accept rate and the cohort forecast are its view."""
    assert "profile:explain" not in PERMS["director"]
    assert as_role(DIRECTOR).get("/explain/anything").status_code == 403


def test_explaining_requires_credentials():
    app.dependency_overrides.clear()
    assert TestClient(app).get("/explain/anything").status_code == 401


# --- the band arithmetic -----------------------------------------------------


def test_the_band_says_how_far_the_next_one_is():
    """"4 points below amber" and "just inside amber" are different
    conversations, and neither is visible from a colour."""
    amber_at = next(t for t, v in BANDS if str(v) == "amber")
    band = _band_for(amber_at - 4)
    assert band["verdict"] == "green"
    assert band["next_verdict"] == "amber"
    assert band["points_to_next"] == 4


def test_the_top_band_has_nowhere_further_to_go():
    highest = max(t for t, _ in BANDS)
    band = _band_for(highest + 10)
    assert band["next_verdict"] is None
    assert band["points_to_next"] is None


def test_a_blocked_assessment_has_no_band():
    """A hard gate ends the pipeline and produces no score, because a number
    beside an absolute contraindication invites someone to weigh it against a
    discount."""
    assert _band_for(None) is None


# --- the explanation itself --------------------------------------------------


def _log_row(ruleset_version: str = RULESET_VERSION):
    return {
        "request_id": "req-1",
        "actor_id": "dr.casey.park",
        "feature_hash": "abc123",
        "ruleset_version": ruleset_version,
        "created_at": "2026-08-17T09:00:00+00:00",
        "result": {
            "assessments": [
                {
                    "rxcui": "861007",
                    "verdict": "amber",
                    "score": 50,
                    "findings": [
                        {"code": "PGX_ACTIONABLE", "weight": 40, "source": "CPIC level A", "stage": 8},
                        {"code": "HEPATIC_IMPAIRED", "weight": 10, "source": "hepatic seed", "stage": 6},
                        {"code": "PGX_STANDARD_DOSING", "weight": 0, "source": "CPIC level A", "stage": 8},
                    ],
                }
            ]
        },
    }


def _explained(monkeypatch, row):
    monkeypatch.setattr("app.main.session_scope", _fake_scope(row))
    return as_role(PHARMACIST).get("/explain/req-1")


def _fake_scope(row):
    """Stand in for the tenant session so the endpoint can be exercised without
    a database, mirroring the row shape session_scope would yield."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    @contextmanager
    def scope(_hospital, _actor):
        class _Scalars:
            def first(self_inner):
                return SimpleNamespace(
                    request_id=row["request_id"],
                    actor_id=row["actor_id"],
                    feature_hash=row["feature_hash"],
                    ruleset_version=row["ruleset_version"],
                    created_at=None,
                    result=row["result"],
                )

        yield SimpleNamespace(scalars=lambda *_a, **_k: _Scalars())

    return scope


def test_contributions_are_ordered_and_share_the_score(monkeypatch):
    body = _explained(monkeypatch, _log_row()).json()
    contributions = body["assessments"][0]["contributions"]
    assert [c["code"] for c in contributions][:2] == ["PGX_ACTIONABLE", "HEPATIC_IMPAIRED"]
    assert contributions[0]["share"] == 0.8  # 40 of 50
    assert contributions[1]["share"] == 0.2  # 10 of 50


def test_an_informational_finding_is_listed_without_a_share(monkeypatch):
    """Weight 0 records what was checked. It must appear -- silence would be
    indistinguishable from never having looked -- but it contributed nothing."""
    body = _explained(monkeypatch, _log_row()).json()
    info = [c for c in body["assessments"][0]["contributions"] if c["code"] == "PGX_STANDARD_DOSING"]
    assert len(info) == 1
    assert info[0]["weight"] == 0
    assert info[0]["share"] == 0.0


def test_an_unchanged_ruleset_carries_no_caveat(monkeypatch):
    body = _explained(monkeypatch, _log_row()).json()
    assert body["explained_with_original_ruleset"] is True
    assert body["caveat"] is None


def test_an_old_assessment_says_so_instead_of_pretending(monkeypatch):
    """§7's warning, as behaviour: explaining a six-month-old decision with
    today's weights would look like a perfectly good answer and be a lie."""
    body = _explained(monkeypatch, _log_row(ruleset_version="2020.01.1")).json()
    assert body["explained_with_original_ruleset"] is False
    assert "2020.01.1" in body["caveat"]
    assert RULESET_VERSION in body["caveat"]


def test_the_published_weight_table_comes_back_with_it(monkeypatch):
    """So a reader can check a weight against the table rather than taking the
    numbers on trust."""
    body = _explained(monkeypatch, _log_row()).json()
    assert body["ruleset"]["weights"]
    assert body["ruleset"]["bands"]
