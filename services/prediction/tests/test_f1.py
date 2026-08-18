"""F1 restock recommendations computed on read."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import Principal, current_principal
from medstock_shared.db import engine, session_scope
from medstock_shared.models import (
    Drug,
    Facility,
    Hospital,
    ParLevel,
    StockBatch,
    StockSnapshot,
    Supplier,
    SupplierCatalog,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

HOSPITAL_A = uuid.UUID("00000000-0000-0000-0000-00000000f1a1")
ACTOR = uuid.UUID("00000000-0000-0000-0000-00000000f1c3")
NDC = "00338011220"
NDC_NOPAR = "16714097720"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_principal, None)


def _wipe() -> None:
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_teardown") as s:
        s.execute(delete(StockBatch).where(StockBatch.hospital_id == HOSPITAL_A))
        s.execute(delete(StockSnapshot).where(StockSnapshot.hospital_id == HOSPITAL_A))
        s.execute(delete(ParLevel).where(ParLevel.hospital_id == HOSPITAL_A))
        sids = list(s.scalars(select(Supplier.id).where(Supplier.hospital_id == HOSPITAL_A)))
        if sids:
            s.execute(delete(SupplierCatalog).where(SupplierCatalog.supplier_id.in_(sids)))
        s.execute(delete(Supplier).where(Supplier.hospital_id == HOSPITAL_A))
        s.execute(delete(Facility).where(Facility.hospital_id == HOSPITAL_A))


def _client() -> TestClient:
    app.dependency_overrides[current_principal] = lambda: Principal(
        str(ACTOR), str(HOSPITAL_A), "pharmacist"
    )
    return TestClient(app)


@pytest.fixture
def seeded():
    _wipe()
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_A, name="F1 HOSPITAL A"))
        s.flush()
        fa = Facility(
            hospital_id=HOSPITAL_A, code="f1-central", name="F1 Central",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        s.add(fa)
        s.flush()
        for ndc, name in ((NDC, "Norepinephrine 4mg/4mL"), (NDC_NOPAR, "Propofol 1% Emulsion")):
            existing = s.scalar(select(Drug).where(Drug.ndc == ndc))
            if existing is None:
                s.add(Drug(ndc=ndc, name=name, raw={"source": "test"}))
        supplier = Supplier(
            hospital_id=HOSPITAL_A,
            name="F1 Pharma",
            lead_time_days=5,
            reliability_pct=Decimal("95.00"),
            shipping_flat=Decimal("60.00"),
            currency="USD",
            active=True,
        )
        s.add(supplier)
        s.flush()
        s.add(SupplierCatalog(supplier_id=supplier.id, ndc=NDC, unit_cost=Decimal("11.4000"), pack_size=10, min_order_qty=10))
        s.add(SupplierCatalog(supplier_id=supplier.id, ndc=NDC_NOPAR, unit_cost=Decimal("8.0000"), pack_size=1, min_order_qty=1))
        s.commit()
        ids = {"a": fa.id, "supplier": supplier.id}
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_f1") as s:
        s.add(ParLevel(hospital_id=HOSPITAL_A, facility_id=ids["a"], ndc=NDC, reorder_point=20, target_qty=180))
        s.add(
            StockBatch(
                hospital_id=HOSPITAL_A, facility_id=ids["a"], ndc=NDC,
                lot="F1-LOT", expiry_date=date.today() + timedelta(days=30),
                quantity=6, location_id="main-room",
            )
        )
        s.add(
            StockBatch(
                hospital_id=HOSPITAL_A, facility_id=ids["a"], ndc=NDC_NOPAR,
                lot="F1-PROP", expiry_date=date.today() + timedelta(days=30),
                quantity=40, location_id="main-room",
            )
        )
    yield ids
    _wipe()


def test_recommendation_uses_par_minus_on_hand(seeded):
    body = _client().get("/recommendations", params={"facility_id": seeded["a"], "ndc": NDC}).json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["ndc"] == NDC
    assert item["quantity"] >= 180 - 6
    assert item["supplier_id"] == seeded["supplier"]
    assert item["rationale"]["target_qty"] == 180
    assert item["rationale"]["on_hand"] == 6


def test_no_par_row_means_no_recommendation(seeded):
    body = _client().get(
        "/recommendations", params={"facility_id": seeded["a"], "ndc": NDC_NOPAR}
    ).json()
    assert body["items"] == []
