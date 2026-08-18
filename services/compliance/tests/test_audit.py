"""H1 append-only audit log — trigger, grants, GET /audit, tenant isolation."""

from __future__ import annotations

import uuid

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import PERMS, Principal, current_principal
from medstock_shared.db import engine, session_scope
from medstock_shared.models import AuditLogEntry, Facility, Hospital, ReviewDecision
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

HOSPITAL_A = uuid.uuid4()
HOSPITAL_B = uuid.uuid4()
ACTOR = uuid.uuid4()


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_principal, None)


@pytest.fixture
def seeded():
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_A, name="AUDIT HOSPITAL A"))
        s.merge(Hospital(id=HOSPITAL_B, name="AUDIT HOSPITAL B"))
        s.flush()
        fa = Facility(
            hospital_id=HOSPITAL_A, code="a-central", name="A Central",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        fb = Facility(
            hospital_id=HOSPITAL_B, code="b-central", name="B Central",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        s.add_all([fa, fb])
        s.commit()
        ids = {"a": fa.id, "b": fb.id}

    yield ids

    with Session(engine) as s:
        s.execute(delete(AuditLogEntry).where(AuditLogEntry.hospital_id.in_([HOSPITAL_A, HOSPITAL_B])))
        s.execute(delete(ReviewDecision).where(ReviewDecision.hospital_id.in_([HOSPITAL_A, HOSPITAL_B])))
        s.execute(delete(Facility).where(Facility.hospital_id.in_([HOSPITAL_A, HOSPITAL_B])))
        s.execute(delete(Hospital).where(Hospital.id.in_([HOSPITAL_A, HOSPITAL_B])))
        s.commit()


def _client(role: str = "pharmacist", hospital_id: uuid.UUID = HOSPITAL_A) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: Principal(
        str(ACTOR), str(hospital_id), role
    )
    return TestClient(app)


def _insert_decision(hospital_id: uuid.UUID, facility_id: int, actor_id: str, actor_system: str = ""):
    with session_scope(str(hospital_id), actor_id, actor_system) as session:
        session.add(
            ReviewDecision(
                hospital_id=hospital_id,
                facility_id=facility_id,
                entity_type="restock_recommendation",
                entity_ref="00000-0000-01",
                decision="approved",
                actor_id=uuid.UUID(actor_id) if actor_id else None,
                payload={"quantity": 12},
            )
        )


def test_pharmacist_holds_audit_read():
    assert "audit:read" in PERMS["pharmacist"]
    assert "audit:read" not in PERMS["physician"]


def test_physician_cannot_read_audit():
    assert _client(role="physician").get("/audit").status_code == 403


def test_unauthenticated_audit_is_401():
    app.dependency_overrides.pop(current_principal, None)
    assert TestClient(app).get("/audit").status_code == 401


def test_approving_writes_an_audit_row_without_calling_audit(seeded):
    _insert_decision(HOSPITAL_A, seeded["a"], str(ACTOR))
    body = _client().get("/audit").json()
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert row["entity_type"] == "review_decision"
    assert row["action"] == "INSERT"
    assert row["actor_id"] == str(ACTOR)
    assert row["actor_system"] is None
    assert row["after"]["decision"] == "approved"


def test_cronjob_write_sets_actor_system_not_actor_id(seeded):
    _insert_decision(HOSPITAL_A, seeded["a"], "", actor_system="prediction-cronjob")
    row = _client().get("/audit").json()["items"][0]
    assert row["actor_id"] is None
    assert row["actor_system"] == "prediction-cronjob"


def test_write_with_no_actor_rolls_back(seeded):
    with pytest.raises(IntegrityError):
        _insert_decision(HOSPITAL_A, seeded["a"], "", actor_system="")
    assert _client().get("/audit").json()["items"] == []


def test_cross_tenant_read_is_empty(seeded):
    _insert_decision(HOSPITAL_A, seeded["a"], str(ACTOR))
    assert _client(hospital_id=HOSPITAL_B).get("/audit").json()["items"] == []
    assert len(_client(hospital_id=HOSPITAL_A).get("/audit").json()["items"]) == 1


def test_entity_filter(seeded):
    _insert_decision(HOSPITAL_A, seeded["a"], str(ACTOR))
    miss = _client().get("/audit", params={"entity": "purchase_order"}).json()
    assert miss["items"] == []
    hit = _client().get("/audit", params={"entity": "review_decision"}).json()
    assert len(hit["items"]) == 1


def test_app_role_cannot_delete_or_update_audit_rows(seeded):
    _insert_decision(HOSPITAL_A, seeded["a"], str(ACTOR))
    # SET hospital_id so RLS would otherwise let the rows through — the
    # failure must be the grant, not an empty-setting miss.
    with engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.hospital_id', :h, true)"),
            {"h": str(HOSPITAL_A)},
        )
        conn.execute(text("SET LOCAL ROLE app_role"))
        with pytest.raises(ProgrammingError):
            conn.execute(text("DELETE FROM audit_log_entry"))
    with engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.hospital_id', :h, true)"),
            {"h": str(HOSPITAL_A)},
        )
        conn.execute(text("SET LOCAL ROLE app_role"))
        with pytest.raises(ProgrammingError):
            conn.execute(text("UPDATE audit_log_entry SET action = 'tamper'"))
