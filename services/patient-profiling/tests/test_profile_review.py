"""PP-5: the approval gate, which until now did not exist.

docs/prognosis-and-procurement.md §1.3 gate 3 says a profile lands
`awaiting_approval` and colours nothing until a pharmacist accepts it. The
extraction job wrote that status, `approved_profiles()` filtered on `approved`,
and no code path in the system could move a row from one to the other. The gate
was not enforced-but-open, it was **absent**: every profile ever extracted was
unreachable, so PP-3 and PP-4 read an empty table on any real deployment while
every test passed, because the tests construct RiskProfile objects directly.

So the tests that matter here are about who may rule, what a ruling records,
and what the accept rate means when nobody has ruled yet.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.main import PROFILE_STATUSES, accept_rate, app, review_update
from fastapi.testclient import TestClient
from medstock_shared.auth import PERMS, Principal, current_principal

NOW = datetime(2026, 8, 17, 9, 30, tzinfo=UTC)

PHARMACIST = Principal("pharm-1", "hospital-1", "pharmacist")
DIRECTOR = Principal("dir-1", "hospital-1", "director")
PHYSICIAN = Principal("phys-1", "hospital-1", "physician")
ADMIN = Principal("admin-1", "hospital-1", "admin")


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def as_role(principal: Principal) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: principal
    return TestClient(app)


# --- who may rule -----------------------------------------------------------


def test_only_a_pharmacist_may_approve():
    """The gate is a clinical judgement on a model's reading of a label. An
    admin can grant itself every operational permission in the table and must
    still not be able to sign off clinical content -- a gate a non-clinician
    passes is not the gate §1.3 claims."""
    holders = {role for role, perms in PERMS.items() if "profile:approve" in perms}
    assert holders == {"pharmacist"}


def test_the_queue_is_readable_by_the_people_who_have_to_act_on_it():
    """Pharmacist to review, director to read the accept rate (§5.4), admin to
    see the backlog. Not the physician -- prescribing is not reviewing."""
    holders = {role for role, perms in PERMS.items() if "profile:review" in perms}
    assert holders == {"pharmacist", "director", "admin"}


def test_a_director_may_read_the_queue_but_not_rule_on_it(monkeypatch):
    monkeypatch.setattr("app.main.load_queue", lambda *_: ([], dict.fromkeys(PROFILE_STATUSES, 0)))
    assert as_role(DIRECTOR).get("/risk-profiles").status_code == 200
    ruling = as_role(DIRECTOR).post("/risk-profiles/1/review", json={"action": "approve"})
    assert ruling.status_code == 403


def test_a_physician_cannot_see_the_queue_at_all():
    assert as_role(PHYSICIAN).get("/risk-profiles").status_code == 403


def test_an_admin_cannot_approve():
    """Named separately from the permission-table test because this is the one
    that would silently regress: admin accumulates permissions."""
    response = as_role(ADMIN).post("/risk-profiles/1/review", json={"action": "approve"})
    assert response.status_code == 403


def test_ruling_requires_credentials():
    app.dependency_overrides.clear()
    response = TestClient(app).post("/risk-profiles/1/review", json={"action": "approve"})
    assert response.status_code == 401


# --- what a ruling records --------------------------------------------------


def test_approval_records_who_and_when():
    assert review_update("approve", "pharm-1", "", NOW) == {
        "status": "approved",
        "reviewed_by": "pharm-1",
        "reviewed_at": NOW,
        "review_note": "",
    }


def test_a_rejection_records_its_reviewer_too():
    """The reason the column is `reviewed_by` and not `approved_by`. A rejection
    with no reviewer is an unattributable clinical decision."""
    update = review_update("reject", "pharm-1", "factors not in the cited section", NOW)
    assert update["status"] == "rejected"
    assert update["reviewed_by"] == "pharm-1"
    assert update["reviewed_at"] == NOW


def test_only_the_two_actions_exist():
    """No 'defer', no 'maybe'. A profile is served or it is not."""
    with pytest.raises(KeyError):
        review_update("approve_later", "pharm-1", "", NOW)


def test_an_unknown_action_is_rejected_at_the_boundary():
    response = as_role(PHARMACIST).post(
        "/risk-profiles/1/review", json={"action": "approve_later"}
    )
    assert response.status_code == 422


def test_a_ruling_reports_the_status_it_overturned(monkeypatch):
    """Withdrawing an approval must be visible as a withdrawal, not look like a
    fresh rejection of something nobody had approved."""
    captured: dict = {}

    def fake_apply(profile_id, updates):
        captured.update(updates)
        return "approved", {"id": profile_id, "status": updates["status"]}

    monkeypatch.setattr("app.main.apply_review", fake_apply)
    response = as_role(PHARMACIST).post(
        "/risk-profiles/7/review", json={"action": "reject", "note": "label revised"}
    )
    assert response.status_code == 200
    assert response.json()["previous_status"] == "approved"
    assert response.json()["profile"]["status"] == "rejected"
    assert captured["reviewed_by"] == "pharm-1"
    assert captured["review_note"] == "label revised"


def test_ruling_on_a_profile_that_is_not_there_is_a_404(monkeypatch):
    monkeypatch.setattr("app.main.apply_review", lambda *_: None)
    response = as_role(PHARMACIST).post("/risk-profiles/999/review", json={"action": "approve"})
    assert response.status_code == 404


# --- the queue --------------------------------------------------------------


def test_the_queue_defaults_to_what_is_awaiting_review(monkeypatch):
    seen: dict = {}

    def fake_queue(status, rxcui, limit):
        seen.update(status=status, rxcui=rxcui, limit=limit)
        return [], dict.fromkeys(PROFILE_STATUSES, 0)

    monkeypatch.setattr("app.main.load_queue", fake_queue)
    as_role(PHARMACIST).get("/risk-profiles")
    assert seen["status"] == "awaiting_approval"


def test_a_status_outside_the_vocabulary_is_refused(monkeypatch):
    monkeypatch.setattr("app.main.load_queue", lambda *_: ([], {}))
    response = as_role(PHARMACIST).get("/risk-profiles", params={"status": "aproved"})
    assert response.status_code == 422


def test_the_page_size_is_capped(monkeypatch):
    seen: dict = {}

    def fake_queue(status, rxcui, limit):
        seen["limit"] = limit
        return [], dict.fromkeys(PROFILE_STATUSES, 0)

    monkeypatch.setattr("app.main.load_queue", fake_queue)
    as_role(PHARMACIST).get("/risk-profiles", params={"limit": 100_000})
    assert seen["limit"] == 200


def test_a_missing_table_is_loud_here_rather_than_an_empty_queue(monkeypatch):
    """The opposite call to approved_profiles(), on purpose. On the request path
    an absent migration degrades to "no prognosis" and the deterministic stages
    still answer. Here an empty list reads as "nothing to review" and the
    backlog becomes invisible."""
    from sqlalchemy.exc import ProgrammingError

    def boom(*_):
        raise ProgrammingError("select", {}, Exception("no such table"))

    monkeypatch.setattr("app.main.load_queue", boom)
    assert as_role(PHARMACIST).get("/risk-profiles").status_code == 503


# --- the accept rate (§5.4) -------------------------------------------------


def test_the_accept_rate_is_unknown_before_anyone_rules():
    """Not 0.0. §5.4 reads the rate against a ~80% threshold, and a rate of zero
    over no decisions would fail that threshold on a queue nobody has opened."""
    assert accept_rate({"awaiting_approval": 40, "approved": 0, "rejected": 0}) is None


def test_awaiting_profiles_do_not_drag_the_rate_down():
    """Otherwise the number measures how far the reviewer has got, not how
    accurate the extraction is."""
    assert accept_rate({"awaiting_approval": 90, "approved": 9, "rejected": 1}) == 0.9


def test_the_rate_spans_both_ends():
    assert accept_rate({"approved": 4, "rejected": 0}) == 1.0
    assert accept_rate({"approved": 0, "rejected": 4}) == 0.0
