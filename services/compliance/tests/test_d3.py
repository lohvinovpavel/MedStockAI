"""D3 streamed compliance CSV export."""

from __future__ import annotations

import uuid

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import PERMS, Principal, current_principal
from medstock_shared.db import engine, session_scope
from medstock_shared.models import AuditLogEntry, Facility, Hospital, ReviewDecision
from sqlalchemy import delete
from sqlalchemy.orm import Session

HOSPITAL_A = uuid.UUID("00000000-0000-0000-0000-00000000d3a1")
HOSPITAL_B = uuid.UUID("00000000-0000-0000-0000-00000000d3b2")
ACTOR = uuid.UUID("00000000-0000-0000-0000-00000000d3c3")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_principal, None)


@pytest.fixture
def seeded():
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_A, name="D3 HOSPITAL A"))
        s.merge(Hospital(id=HOSPITAL_B, name="D3 HOSPITAL B"))
        s.flush()
        fa = Facility(
            hospital_id=HOSPITAL_A, code="d3-a", name="D3 A",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        fb = Facility(
            hospital_id=HOSPITAL_B, code="d3-b", name="D3 B",
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


def _client(role: str = "director", hospital_id: uuid.UUID = HOSPITAL_A) -> TestClient:
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
                entity_ref="00338011220",
                decision="approved",
                actor_id=uuid.UUID(actor_id) if actor_id else None,
                payload={"ndc": "00338011220", "quantity": 12},
            )
        )


def test_pharmacist_holds_read_but_not_export():
    assert "audit:read" in PERMS["pharmacist"]
    assert "audit:export" not in PERMS["pharmacist"]
    assert "audit:export" in PERMS["director"]


def test_pharmacist_export_is_403(seeded):
    assert _client("pharmacist").get("/export/compliance.csv").status_code == 403


def test_formula_cell_is_neutralised_and_actor_is_never_empty(seeded):
    _insert_decision(HOSPITAL_A, seeded["a"], "", actor_system="=SUM(1)")
    res = _client().get("/export/compliance.csv")
    assert res.status_code == 200
    text = res.text
    assert "text/csv" in res.headers["content-type"]
    rows = [line for line in text.splitlines() if line]
    assert rows[0].startswith("occurred_at,actor,")
    body = rows[1]
    assert "'=SUM(1)" in body
    actor = body.split(",")[1]
    assert actor.strip("'") != ""


def test_exports_are_byte_identical(seeded):
    _insert_decision(HOSPITAL_A, seeded["a"], str(ACTOR))
    first = _client().get("/export/compliance.csv").content
    second = _client().get("/export/compliance.csv").content
    assert first == second
    assert first.count(b"\n") >= 2


def test_cross_tenant_export_is_empty(seeded):
    _insert_decision(HOSPITAL_A, seeded["a"], str(ACTOR))
    other = _client(hospital_id=HOSPITAL_B).get("/export/compliance.csv").text
    assert other.strip().count("\n") == 0 or len(other.splitlines()) == 1
