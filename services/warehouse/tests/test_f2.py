"""Wave 4 F2: supplier catalog and Decimal quotes."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from app.main import app
from app.pricing import adjust_quantity, quote_totals
from fastapi.testclient import TestClient
from medstock_shared.auth import PERMS, Principal, current_principal
from medstock_shared.db import engine, session_scope
from medstock_shared.models import Facility, Hospital, Supplier, SupplierCatalog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

HOSPITAL_A = uuid.UUID("00000000-0000-0000-0000-00000000f2a1")
HOSPITAL_B = uuid.UUID("00000000-0000-0000-0000-00000000f2b2")
ACTOR = uuid.UUID("00000000-0000-0000-0000-00000000f2c3")
NDC = "00338011220"
NDC_OTHER = "82804006601"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_principal, None)


def _wipe_clean(hospital_id: uuid.UUID) -> None:
    with session_scope(str(hospital_id), str(ACTOR), "test_teardown") as s:
        sids = list(s.scalars(select(Supplier.id).where(Supplier.hospital_id == hospital_id)))
        if sids:
            s.execute(delete(SupplierCatalog).where(SupplierCatalog.supplier_id.in_(sids)))
        s.execute(delete(Supplier).where(Supplier.hospital_id == hospital_id))
        s.execute(delete(Facility).where(Facility.hospital_id == hospital_id))


def _client(role: str = "pharmacist", hospital_id: uuid.UUID = HOSPITAL_A) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: Principal(
        str(ACTOR), str(hospital_id), role
    )
    return TestClient(app)


@pytest.fixture
def seeded():
    _wipe_clean(HOSPITAL_A)
    _wipe_clean(HOSPITAL_B)
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_A, name="WAVE4 F2 HOSPITAL A"))
        s.merge(Hospital(id=HOSPITAL_B, name="WAVE4 F2 HOSPITAL B"))
        s.flush()
        fa = Facility(
            hospital_id=HOSPITAL_A, code="f2-central", name="F2 Central",
            type="Hospital", lat=50.45, lon=30.523, operated=True,
        )
        fb = Facility(
            hospital_id=HOSPITAL_B, code="f2-b", name="F2 B",
            type="Hospital", lat=50.45, lon=30.523, operated=True,
        )
        s.add_all([fa, fb])
        s.flush()
        fast = Supplier(
            hospital_id=HOSPITAL_A,
            name="EuroPharm Wholesale AG",
            lead_time_days=3,
            reliability_pct=Decimal("99.10"),
            shipping_flat=Decimal("210.00"),
            currency="USD",
            active=True,
        )
        mid = Supplier(
            hospital_id=HOSPITAL_A,
            name="PharmaSource Global Ltd.",
            lead_time_days=5,
            reliability_pct=Decimal("98.20"),
            shipping_flat=Decimal("120.00"),
            currency="USD",
            active=True,
        )
        dead = Supplier(
            hospital_id=HOSPITAL_A,
            name="Inactive Historical Co.",
            lead_time_days=9,
            reliability_pct=Decimal("80.00"),
            shipping_flat=Decimal("10.00"),
            currency="USD",
            active=False,
        )
        other = Supplier(
            hospital_id=HOSPITAL_B,
            name="Other Tenant Wholesale",
            lead_time_days=4,
            reliability_pct=Decimal("90.00"),
            shipping_flat=Decimal("50.00"),
            currency="USD",
            active=True,
        )
        s.add_all([fast, mid, dead, other])
        s.flush()
        s.add_all(
            [
                SupplierCatalog(
                    supplier_id=fast.id, ndc=NDC, unit_cost=Decimal("24.8000"),
                    pack_size=10, min_order_qty=10,
                ),
                SupplierCatalog(
                    supplier_id=mid.id, ndc=NDC, unit_cost=Decimal("11.4000"),
                    pack_size=10, min_order_qty=10,
                ),
                SupplierCatalog(
                    supplier_id=dead.id, ndc=NDC, unit_cost=Decimal("9.0000"),
                    pack_size=1, min_order_qty=1,
                ),
                SupplierCatalog(
                    supplier_id=other.id, ndc=NDC, unit_cost=Decimal("1.0000"),
                    pack_size=1, min_order_qty=1,
                ),
            ]
        )
        s.commit()
        ids = {
            "a": fa.id, "b": fb.id,
            "fast": fast.id, "mid": mid.id, "dead": dead.id, "other": other.id,
        }
    yield ids
    _wipe_clean(HOSPITAL_A)
    _wipe_clean(HOSPITAL_B)


def test_order_read_roles():
    assert "order:read" in PERMS["pharmacist"]
    assert "order:read" in PERMS["admin"]
    assert "order:read" in PERMS["director"]
    assert "order:read" not in PERMS["physician"]


def test_pack_size_and_min_order_helpers():
    assert adjust_quantity(145, 10, 10) == (150, "pack_size")
    assert adjust_quantity(10, 10, 10) == (10, None)
    assert adjust_quantity(3, 10, 10) == (10, "min_order_qty")


def test_thousand_line_quote_has_no_float_drift():
    lines = [
        {
            "ndc": f"ndc-{i}",
            "requested": 1,
            "rounded_to": 1,
            "unit_cost": Decimal("1.10"),
            "reason": None,
        }
        for i in range(1000)
    ]
    body = quote_totals(
        lead_time_days=5,
        shipping_flat=Decimal("60.00"),
        lines=lines,
        today=date(2026, 8, 15),
    )
    assert body["subtotal"] == 1100.0
    assert body["shipping"] == 60.0
    assert body["total"] == 1160.0
    assert Decimal(str(body["subtotal"])) == Decimal("1100.00")


def test_physician_cannot_list_suppliers(seeded):
    assert _client("physician").get("/suppliers").status_code == 403


def test_list_includes_inactive_and_hides_other_tenant(seeded):
    items = _client().get("/suppliers", params={"facility_id": seeded["a"]}).json()["items"]
    names = {row["name"] for row in items}
    assert "EuroPharm Wholesale AG" in names
    assert "PharmaSource Global Ltd." in names
    assert "Inactive Historical Co." in names
    assert "Other Tenant Wholesale" not in names
    inactive = next(row for row in items if row["name"] == "Inactive Historical Co.")
    assert inactive["active"] is False


def test_catalog_filters_ndc(seeded):
    body = _client().get(
        f"/suppliers/{seeded['mid']}/catalog", params={"ndc": NDC}
    ).json()
    assert body["supplier_id"] == seeded["mid"]
    assert body["items"] == [
        {"ndc": NDC, "unit_cost": 11.4, "pack_size": 10, "min_order_qty": 10}
    ]
    empty = _client().get(
        f"/suppliers/{seeded['mid']}/catalog", params={"ndc": NDC_OTHER}
    ).json()
    assert empty["items"] == []


def test_quote_rounds_pack_and_names_missing_ndc(seeded):
    c = _client()
    ok = c.post(
        "/quote",
        json={
            "supplier_id": seeded["mid"],
            "facility_id": seeded["a"],
            "lines": [{"ndc": NDC, "quantity": 145}],
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["subtotal"] == 1710.0
    assert body["shipping"] == 120.0
    assert body["total"] == 1830.0
    assert body["lead_time_days"] == 5
    assert body["calendar"] == "calendar_days"
    assert body["expected_delivery"] == (datetime.now(tz=UTC).date() + timedelta(days=5)).isoformat()
    assert body["adjustments"] == [
        {"ndc": NDC, "requested": 145, "rounded_to": 150, "reason": "pack_size"}
    ]
    missing = c.post(
        "/quote",
        json={
            "supplier_id": seeded["mid"],
            "facility_id": seeded["a"],
            "lines": [{"ndc": NDC_OTHER, "quantity": 10}],
        },
    )
    assert missing.status_code == 422
    assert NDC_OTHER in missing.json()["detail"]


def test_quote_rejects_inactive_but_list_still_returns_it(seeded):
    listed = _client().get("/suppliers").json()["items"]
    assert any(row["id"] == seeded["dead"] and row["active"] is False for row in listed)
    res = _client().post(
        "/quote",
        json={
            "supplier_id": seeded["dead"],
            "facility_id": seeded["a"],
            "lines": [{"ndc": NDC, "quantity": 10}],
        },
    )
    assert res.status_code == 422
    assert res.json()["detail"] == "supplier_inactive"


def test_changing_supplier_changes_total_and_delivery(seeded):
    c = _client()
    payload = {"facility_id": seeded["a"], "lines": [{"ndc": NDC, "quantity": 150}]}
    mid = c.post("/quote", json={"supplier_id": seeded["mid"], **payload}).json()
    fast = c.post("/quote", json={"supplier_id": seeded["fast"], **payload}).json()
    assert mid["total"] != fast["total"]
    assert mid["expected_delivery"] != fast["expected_delivery"]
    assert fast["lead_time_days"] == 3
    assert mid["lead_time_days"] == 5
