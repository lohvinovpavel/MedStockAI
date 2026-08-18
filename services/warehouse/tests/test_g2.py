"""Wave 5 G2: inter-facility transfers move stock in the same transaction."""

from __future__ import annotations

import threading
import uuid
from datetime import date, timedelta

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import Principal, current_principal
from medstock_shared.db import engine, session_scope
from medstock_shared.models import Facility, Hospital, StockBatch, StockSnapshot, TransferRequest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

HOSPITAL_A = uuid.UUID("00000000-0000-0000-0000-0000000062a1")
HOSPITAL_B = uuid.UUID("00000000-0000-0000-0000-0000000062b2")
ACTOR = uuid.UUID("00000000-0000-0000-0000-0000000062c3")
NDC = "00338011220"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_principal, None)


def _wipe(hospital_id: uuid.UUID) -> None:
    with session_scope(str(hospital_id), str(ACTOR), "test_teardown") as s:
        s.execute(delete(TransferRequest).where(TransferRequest.hospital_id == hospital_id))
        s.execute(delete(StockBatch).where(StockBatch.hospital_id == hospital_id))
        s.execute(delete(StockSnapshot).where(StockSnapshot.hospital_id == hospital_id))
        s.execute(delete(Facility).where(Facility.hospital_id == hospital_id))


def _client(role: str = "pharmacist", hospital_id: uuid.UUID = HOSPITAL_A) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: Principal(
        str(ACTOR), str(hospital_id), role
    )
    return TestClient(app)


def _qty(facility_id: int) -> int:
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_read") as s:
        value = s.execute(
            select(func.coalesce(func.sum(StockBatch.quantity), 0)).where(
                StockBatch.facility_id == facility_id, StockBatch.ndc == NDC
            )
        ).scalar_one()
        return int(value or 0)


@pytest.fixture
def seeded():
    _wipe(HOSPITAL_A)
    _wipe(HOSPITAL_B)
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_A, name="G2 HOSPITAL A"))
        s.merge(Hospital(id=HOSPITAL_B, name="G2 HOSPITAL B"))
        s.flush()
        src = Facility(
            hospital_id=HOSPITAL_A, code="g2-wh", name="G2 Warehouse",
            type="Warehouse", lat=50.75, lon=30.52, operated=True,
        )
        dest = Facility(
            hospital_id=HOSPITAL_A, code="g2-central", name="G2 Central",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        other = Facility(
            hospital_id=HOSPITAL_B, code="g2-b", name="G2 B",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        s.add_all([src, dest, other])
        s.flush()
        s.commit()
        ids = {"src": src.id, "dest": dest.id, "other": other.id}
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_g2") as s:
        s.add(
            StockBatch(
                hospital_id=HOSPITAL_A,
                facility_id=ids["src"],
                ndc=NDC,
                lot="NOR-25A",
                expiry_date=date.today() + timedelta(days=40),
                quantity=30,
                location_id="main-room",
            )
        )
    yield ids
    _wipe(HOSPITAL_A)
    _wipe(HOSPITAL_B)


def _create(seeded, quantity=30):
    return _client().post(
        "/transfers",
        json={
            "from_facility_id": seeded["src"],
            "to_facility_id": seeded["dest"],
            "ndc": NDC,
            "quantity": quantity,
        },
    )


def test_physician_cannot_write_transfer(seeded):
    assert _client("physician").post(
        "/transfers",
        json={
            "from_facility_id": seeded["src"],
            "to_facility_id": seeded["dest"],
            "ndc": NDC,
            "quantity": 1,
        },
    ).status_code == 403


def test_dispatch_reduces_source_not_destination(seeded):
    created = _create(seeded).json()
    assert created["status"] == "requested"
    dispatched = _client().patch(
        f"/transfers/{created['id']}/status", json={"status": "dispatched"}
    ).json()
    assert dispatched["status"] == "dispatched"
    assert dispatched["lines_reserved"][0]["lot"] == "NOR-25A"
    assert _qty(seeded["src"]) == 0
    assert _qty(seeded["dest"]) == 0


def test_receive_preserves_lot_and_expiry(seeded):
    created = _create(seeded).json()
    _client().patch(f"/transfers/{created['id']}/status", json={"status": "dispatched"})
    received = _client().patch(
        f"/transfers/{created['id']}/status", json={"status": "received"}
    ).json()
    assert received["status"] == "received"
    assert _qty(seeded["src"]) == 0
    assert _qty(seeded["dest"]) == 30
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_read") as s:
        batch = s.scalar(
            select(StockBatch).where(
                StockBatch.facility_id == seeded["dest"], StockBatch.ndc == NDC
            )
        )
        assert batch.lot == "NOR-25A"
        assert batch.expiry_date == date.today() + timedelta(days=40)


def test_over_dispatch_is_422_and_moves_nothing(seeded):
    created = _create(seeded, quantity=99).json()
    res = _client().patch(f"/transfers/{created['id']}/status", json={"status": "dispatched"})
    assert res.status_code == 422
    assert _qty(seeded["src"]) == 30
    assert _qty(seeded["dest"]) == 0


def test_cancel_dispatched_restores_source(seeded):
    created = _create(seeded).json()
    _client().patch(f"/transfers/{created['id']}/status", json={"status": "dispatched"})
    assert _qty(seeded["src"]) == 0
    cancelled = _client().patch(
        f"/transfers/{created['id']}/status", json={"status": "cancelled"}
    ).json()
    assert cancelled["status"] == "cancelled"
    assert _qty(seeded["src"]) == 30
    assert _qty(seeded["dest"]) == 0


def test_same_facility_rejected(seeded):
    res = _client().post(
        "/transfers",
        json={
            "from_facility_id": seeded["src"],
            "to_facility_id": seeded["src"],
            "ndc": NDC,
            "quantity": 1,
        },
    )
    assert res.status_code == 422


def test_concurrent_dispatch_cannot_both_succeed(seeded):
    first = _create(seeded, quantity=30).json()
    second = _create(seeded, quantity=30).json()
    codes: list[int] = []

    def _dispatch(tid: int) -> None:
        client = _client()
        codes.append(client.patch(f"/transfers/{tid}/status", json={"status": "dispatched"}).status_code)

    t1 = threading.Thread(target=_dispatch, args=(first["id"],))
    t2 = threading.Thread(target=_dispatch, args=(second["id"],))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sorted(codes) == [200, 422]
    assert _qty(seeded["src"]) == 0
