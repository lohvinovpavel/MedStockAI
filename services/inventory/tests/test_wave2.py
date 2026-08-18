"""Wave 2: B2 /items, B4 batches, B5 par, A4 tenant isolation on stock."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import PERMS, Principal, current_principal
from medstock_shared.db import engine, session_scope
from medstock_shared.models import (
    Drug,
    Facility,
    Hospital,
    ParLevel,
    StockBatch,
    StockSnapshot,
)
from medstock_shared.stock import derive_status, suggested_order_qty
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

HOSPITAL_A = uuid.UUID("00000000-0000-0000-0000-00000000b2a1")
HOSPITAL_B = uuid.UUID("00000000-0000-0000-0000-00000000b2b2")
ACTOR = uuid.UUID("00000000-0000-0000-0000-00000000b2c3")
NDC = "11111000001"
NDC_B = "22222000002"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_principal, None)


def _wipe_tenant(hospital_id: uuid.UUID) -> None:
    """Deletes must carry H1 GUCs — the audit trigger stamps hospital_id from
    `app.hospital_id`, and `''::uuid` is a DataError rather than a skip."""
    with session_scope(str(hospital_id), str(ACTOR), "test_teardown") as s:
        s.execute(delete(ParLevel).where(ParLevel.hospital_id == hospital_id))
        s.execute(delete(StockBatch).where(StockBatch.hospital_id == hospital_id))
        s.execute(delete(StockSnapshot).where(StockSnapshot.hospital_id == hospital_id))
        s.execute(delete(Facility).where(Facility.hospital_id == hospital_id))


@pytest.fixture
def seeded():
    _wipe_tenant(HOSPITAL_A)
    _wipe_tenant(HOSPITAL_B)
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_A, name="WAVE2 HOSPITAL A"))
        s.merge(Hospital(id=HOSPITAL_B, name="WAVE2 HOSPITAL B"))
        s.flush()
        fa = Facility(
            hospital_id=HOSPITAL_A, code="w2-central", name="W2 Central",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        riverside = Facility(
            hospital_id=HOSPITAL_A, code="w2-riverside", name="W2 Riverside",
            type="Clinic", lat=50.46, lon=30.53, operated=True,
        )
        partner = Facility(
            hospital_id=HOSPITAL_A, code="w2-partner", name="W2 Partner",
            type="Pharmacy", lat=50.5, lon=30.6, operated=False,
        )
        fb = Facility(
            hospital_id=HOSPITAL_B, code="w2-b", name="W2 B",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        s.add_all([fa, riverside, partner, fb])
        s.flush()
        for ndc, name in ((NDC, "Wave2cillin 500 MG"), (NDC_B, "Wave2statin 20 MG")):
            existing = s.scalar(select(Drug).where(Drug.ndc == ndc))
            if existing is None:
                s.add(Drug(ndc=ndc, name=name, raw={"source": "test"}))
            else:
                existing.name = name
        s.commit()
        ids = {"a": fa.id, "riverside": riverside.id, "partner": partner.id, "b": fb.id}

    yield ids

    _wipe_tenant(HOSPITAL_A)
    _wipe_tenant(HOSPITAL_B)


def _client(role: str = "pharmacist", hospital_id: uuid.UUID = HOSPITAL_A) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: Principal(
        str(ACTOR), str(hospital_id), role
    )
    return TestClient(app)


def test_status_bands_are_table_driven():
    cases = [
        (0, 10, 40, "stockout", True),
        (5, 10, 40, "critical", True),
        (20, 10, 40, "normal", True),
        (80, 10, 40, "surplus", True),
        (0, None, None, "stockout", False),
        (50, None, None, "normal", False),
    ]
    for qty, reorder, target, status, par in cases:
        got_status, got_par = derive_status(qty, reorder, target)
        assert (got_status, got_par) == (status, par), (qty, reorder, target)
        if status != "critical" or not par:
            assert got_status != "critical" or par


def test_no_par_never_critical():
    status, par = derive_status(1, None, None)
    assert status == "normal"
    assert par is False


def test_suggested_qty_is_independent_of_endpoints():
    assert suggested_order_qty(10, 40) == 30
    assert suggested_order_qty(50, 40) == 0


def test_pharmacist_holds_batch_write():
    assert "batch:write" in PERMS["pharmacist"]
    assert "batch:write" in PERMS["admin"]
    assert "par:write" in PERMS["admin"]
    assert "par:write" not in PERMS["pharmacist"]


def test_items_limit_over_200_is_422():
    assert _client().get("/items", params={"limit": 201}).status_code == 422


def test_empty_stock_is_200_not_404(seeded, monkeypatch):
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: ["00000000000"])
    res = _client().get("/stock", params={"rxcui": "1"})
    assert res.status_code == 200
    assert res.json()["items"] == []


def test_rxnorm_failure_degrades(seeded, monkeypatch):
    from medstock_shared.rxnorm import RxNormError

    monkeypatch.setattr(
        "app.main.ndcs_for_rxcui",
        lambda rxcui: (_ for _ in ()).throw(RxNormError("down")),
    )
    res = _client().get("/stock", params={"rxcui": NDC})
    assert res.status_code == 200
    assert res.json()["rxnorm_degraded"] is True


def test_receive_same_lot_adds_and_rollups(seeded):
    expiry = (date.today() + timedelta(days=40)).isoformat()
    c = _client()
    first = c.post(
        "/batches",
        json={
            "facility_id": seeded["a"],
            "ndc": NDC,
            "lot": "LOT-A",
            "expiry_date": expiry,
            "quantity": 120,
            "location_id": "main-room",
        },
    )
    assert first.status_code == 201, first.text
    second = c.post(
        "/batches",
        json={
            "facility_id": seeded["a"],
            "ndc": NDC,
            "lot": "LOT-A",
            "expiry_date": expiry,
            "quantity": 40,
            "location_id": "main-room",
        },
    )
    assert second.status_code == 201
    assert second.json()["quantity"] == 160
    assert second.json()["snapshot_quantity"] == 160
    items = c.get("/items", params={"facility_id": seeded["a"]}).json()["items"]
    assert len(items) == 1
    assert items[0]["quantity"] == 160


def test_two_lots_sum_on_snapshot(seeded):
    expiry = (date.today() + timedelta(days=40)).isoformat()
    c = _client()
    c.post(
        "/batches",
        json={
            "facility_id": seeded["a"], "ndc": NDC, "lot": "L1",
            "expiry_date": expiry, "quantity": 10, "location_id": "main-room",
        },
    )
    c.post(
        "/batches",
        json={
            "facility_id": seeded["a"], "ndc": NDC, "lot": "L2",
            "expiry_date": (date.today() + timedelta(days=10)).isoformat(),
            "quantity": 15, "location_id": "main-room",
        },
    )
    batches = c.get("/batches", params={"ndc": NDC, "facility_id": seeded["a"]}).json()["items"]
    assert [b["lot"] for b in batches] == ["L2", "L1"]
    item = c.get("/items", params={"facility_id": seeded["a"]}).json()["items"][0]
    assert item["quantity"] == 25
    assert item["lot"] == "L2"


def test_past_expiry_rejected(seeded):
    res = _client().post(
        "/batches",
        json={
            "facility_id": seeded["a"], "ndc": NDC, "lot": "OLD",
            "expiry_date": "2000-01-01", "quantity": 10, "location_id": "main-room",
        },
    )
    assert res.status_code == 422


def test_non_operated_receive_is_422(seeded):
    res = _client().post(
        "/batches",
        json={
            "facility_id": seeded["partner"], "ndc": NDC, "lot": "X",
            "expiry_date": (date.today() + timedelta(days=10)).isoformat(),
            "quantity": 10, "location_id": "main-room",
        },
    )
    assert res.status_code == 422


def test_consume_over_qty_is_422_and_unchanged(seeded):
    expiry = (date.today() + timedelta(days=40)).isoformat()
    c = _client()
    created = c.post(
        "/batches",
        json={
            "facility_id": seeded["a"], "ndc": NDC, "lot": "C1",
            "expiry_date": expiry, "quantity": 20, "location_id": "main-room",
        },
    ).json()
    res = c.post(f"/batches/{created['id']}/consume", json={"quantity": 50, "reason": "dispense"})
    assert res.status_code == 422
    assert c.get("/batches", params={"ndc": NDC}).json()["items"][0]["quantity"] == 20


def test_disjoint_facilities(seeded):
    expiry = (date.today() + timedelta(days=40)).isoformat()
    c = _client()
    c.post(
        "/batches",
        json={
            "facility_id": seeded["a"], "ndc": NDC, "lot": "A",
            "expiry_date": expiry, "quantity": 5, "location_id": "main-room",
        },
    )
    c.post(
        "/batches",
        json={
            "facility_id": seeded["riverside"], "ndc": NDC_B, "lot": "B",
            "expiry_date": expiry, "quantity": 7, "location_id": "main-room",
        },
    )
    a_ndcs = {i["ndc"] for i in c.get("/items", params={"facility_id": seeded["a"]}).json()["items"]}
    r_ndcs = {
        i["ndc"]
        for i in c.get("/items", params={"facility_id": seeded["riverside"]}).json()["items"]
    }
    assert a_ndcs == {NDC}
    assert r_ndcs == {NDC_B}


def test_cross_tenant_facility_is_404(seeded):
    res = _client().get("/items", params={"facility_id": seeded["b"]})
    assert res.status_code == 404


def test_par_upsert_and_status(seeded):
    expiry = (date.today() + timedelta(days=40)).isoformat()
    pharm = _client()
    pharm.post(
        "/batches",
        json={
            "facility_id": seeded["a"], "ndc": NDC, "lot": "P",
            "expiry_date": expiry, "quantity": 5, "location_id": "main-room",
        },
    )
    admin = _client(role="admin")
    first = admin.put(
        "/par-levels",
        json={"facility_id": seeded["a"], "ndc": NDC, "reorder_point": 10, "target_qty": 40},
    )
    assert first.status_code == 200
    second = admin.put(
        "/par-levels",
        json={"facility_id": seeded["a"], "ndc": NDC, "reorder_point": 8, "target_qty": 30},
    )
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["reorder_point"] == 8
    items = pharm.get("/items", params={"facility_id": seeded["a"]}).json()["items"]
    assert items[0]["status"] == "critical"
    assert items[0]["par_defined"] is True


def test_par_constraint_holds_without_api(seeded):
    with pytest.raises(IntegrityError):
        with session_scope(str(HOSPITAL_A), str(ACTOR), "test") as s:
            s.add(
                ParLevel(
                    hospital_id=HOSPITAL_A,
                    facility_id=seeded["a"],
                    ndc=NDC,
                    reorder_point=10,
                    target_qty=10,
                )
            )


def test_physician_cannot_receive(seeded):
    res = _client(role="physician").post(
        "/batches",
        json={
            "facility_id": seeded["a"], "ndc": NDC, "lot": "X",
            "expiry_date": (date.today() + timedelta(days=10)).isoformat(),
            "quantity": 1, "location_id": "main-room",
        },
    )
    assert res.status_code == 403


def test_cross_tenant_read_is_empty(seeded):
    expiry = (date.today() + timedelta(days=40)).isoformat()
    _client().post(
        "/batches",
        json={
            "facility_id": seeded["a"], "ndc": NDC, "lot": "T",
            "expiry_date": expiry, "quantity": 9, "location_id": "main-room",
        },
    )
    other = _client(hospital_id=HOSPITAL_B)
    assert other.get("/items").json()["items"] == []


def test_query_without_session_scope_returns_zero(seeded):
    expiry = date.today() + timedelta(days=40)
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test") as s:
        s.add(
            StockBatch(
                hospital_id=HOSPITAL_A,
                facility_id=seeded["a"],
                ndc=NDC,
                lot="Z",
                expiry_date=expiry,
                quantity=3,
                location_id="main-room",
            )
        )
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE app_role"))
        n = conn.execute(text("SELECT count(*) FROM stock_batch")).scalar()
    assert n == 0


def test_mismatched_hospital_id_fails_with_check(seeded):
    expiry = date.today() + timedelta(days=40)
    with pytest.raises(Exception):
        with session_scope(str(HOSPITAL_A), str(ACTOR), "test") as s:
            s.add(
                StockBatch(
                    hospital_id=HOSPITAL_B,
                    facility_id=seeded["b"],
                    ndc=NDC,
                    lot="BAD",
                    expiry_date=expiry,
                    quantity=1,
                    location_id="main-room",
                )
            )


def test_protected_routes_require_auth():
    bare = TestClient(app)
    public = {"/healthz", "/readyz", "/version", "/docs", "/openapi.json", "/redoc"}
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if (
            not path
            or path in public
            or path.startswith("/docs")
            or path.startswith("/redoc")
            or path.startswith("/api/inventory")
        ):
            continue
        if "GET" in methods:
            assert bare.get(path).status_code in (401, 405, 422), path
        if "POST" in methods:
            assert bare.post(path).status_code in (401, 405, 422), path
