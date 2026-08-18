"""Wave 5: F3 purchase-order lifecycle, F4 history, F1 recommendation writers."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
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
    PurchaseOrder,
    PurchaseOrderLine,
    ReviewDecision,
    StockBatch,
    StockSnapshot,
    Supplier,
    SupplierCatalog,
)
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

HOSPITAL_A = uuid.UUID("00000000-0000-0000-0000-00000000a5a1")
HOSPITAL_B = uuid.UUID("00000000-0000-0000-0000-00000000a5b2")
ACTOR = uuid.UUID("00000000-0000-0000-0000-00000000a5c3")
NDC = "00338011220"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_principal, None)


def _wipe(hospital_id: uuid.UUID) -> None:
    with session_scope(str(hospital_id), str(ACTOR), "test_teardown") as s:
        s.execute(delete(PurchaseOrderLine).where(
            PurchaseOrderLine.purchase_order_id.in_(
                select(PurchaseOrder.id).where(PurchaseOrder.hospital_id == hospital_id)
            )
        ))
        s.execute(delete(PurchaseOrder).where(PurchaseOrder.hospital_id == hospital_id))
        s.execute(delete(ReviewDecision).where(ReviewDecision.hospital_id == hospital_id))
        s.execute(delete(StockBatch).where(StockBatch.hospital_id == hospital_id))
        s.execute(delete(StockSnapshot).where(StockSnapshot.hospital_id == hospital_id))
        s.execute(delete(ParLevel).where(ParLevel.hospital_id == hospital_id))
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
    _wipe(HOSPITAL_A)
    _wipe(HOSPITAL_B)
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_A, name="WAVE5 HOSPITAL A"))
        s.merge(Hospital(id=HOSPITAL_B, name="WAVE5 HOSPITAL B"))
        s.flush()
        fa = Facility(
            hospital_id=HOSPITAL_A, code="w5-central", name="W5 Central",
            type="Hospital", lat=50.45, lon=30.523, operated=True,
        )
        partner = Facility(
            hospital_id=HOSPITAL_A, code="w5-luke", name="W5 Partner",
            type="Hospital", lat=50.45, lon=30.69, operated=False,
        )
        fb = Facility(
            hospital_id=HOSPITAL_B, code="w5-b", name="W5 B",
            type="Hospital", lat=50.45, lon=30.523, operated=True,
        )
        s.add_all([fa, partner, fb])
        s.flush()
        existing = s.scalar(select(Drug).where(Drug.ndc == NDC))
        if existing is None:
            s.add(Drug(ndc=NDC, name="Norepinephrine 4mg/4mL", raw={"rxcui": "1049640"}))
        supplier = Supplier(
            hospital_id=HOSPITAL_A,
            name="Wave5 Pharma",
            lead_time_days=5,
            reliability_pct=Decimal("95.00"),
            shipping_flat=Decimal("60.00"),
            currency="USD",
            active=True,
        )
        s.add(supplier)
        s.flush()
        s.add(
            SupplierCatalog(
                supplier_id=supplier.id, ndc=NDC, unit_cost=Decimal("11.4000"),
                pack_size=10, min_order_qty=10,
            )
        )
        s.commit()
        ids = {"a": fa.id, "partner": partner.id, "b": fb.id, "supplier": supplier.id}
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_w5") as s:
        s.add(
            ParLevel(
                hospital_id=HOSPITAL_A, facility_id=ids["a"], ndc=NDC,
                reorder_point=20, target_qty=180,
            )
        )
        s.add(
            StockBatch(
                hospital_id=HOSPITAL_A, facility_id=ids["a"], ndc=NDC,
                lot="NEP-SEED", expiry_date=datetime.now(tz=UTC).date() + timedelta(days=90),
                quantity=6, location_id="main-room",
            )
        )
    yield ids
    _wipe(HOSPITAL_A)
    _wipe(HOSPITAL_B)


def _place(seeded, *, status="placed", source="manual", quantity=20, headers=None):
    return _client().post(
        "/orders",
        json={
            "facility_id": seeded["a"],
            "supplier_id": seeded["supplier"],
            "status": status,
            "source": source,
            "lines": [{"ndc": NDC, "quantity": quantity}],
        },
        headers=headers or {},
    )


def test_physician_cannot_write_orders(seeded):
    res = _client("physician").post(
        "/orders",
        json={
            "facility_id": seeded["a"],
            "supplier_id": seeded["supplier"],
            "status": "placed",
            "source": "manual",
            "lines": [{"ndc": NDC, "quantity": 10}],
        },
    )
    assert res.status_code == 403


def test_draft_to_delivered_is_409(seeded):
    created = _place(seeded, status="draft").json()
    res = _client().patch(f"/orders/{created['id']}/status", json={"status": "delivered"})
    assert res.status_code == 409
    assert res.json()["detail"]["status"] == "draft"


def test_delivered_creates_stock_batch(seeded):
    created = _place(seeded, status="placed").json()
    oid = created["id"]
    assert _client().patch(f"/orders/{oid}/status", json={"status": "in_transit"}).status_code == 200
    res = _client().patch(
        f"/orders/{oid}/status",
        json={
            "status": "delivered",
            "lines": [{"ndc": NDC, "lot": "NEP-RECV", "expiry_date": "2027-01-15"}],
        },
    )
    assert res.status_code == 200
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_read") as s:
        batch = s.scalar(
            select(StockBatch).where(
                StockBatch.facility_id == seeded["a"],
                StockBatch.ndc == NDC,
                StockBatch.lot == "NEP-RECV",
            )
        )
        assert batch is not None
        assert int(batch.quantity) == created["quantity"]
        assert batch.expiry_date == date(2027, 1, 15)


def test_delete_placed_rejected_cancel_succeeds(seeded):
    created = _place(seeded, status="placed").json()
    assert _client().delete(f"/orders/{created['id']}").status_code == 409
    res = _client().patch(f"/orders/{created['id']}/status", json={"status": "cancelled"})
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"


def test_two_creates_get_distinct_refs(seeded):
    first = _place(seeded)
    second = _place(seeded)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["ref"] != second.json()["ref"]


def test_idempotency_key_creates_one_order(seeded):
    headers = {"Idempotency-Key": "wave5-once"}
    first = _place(seeded, headers=headers)
    second = _place(seeded, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    listed = _client().get("/orders").json()
    assert listed["total"] == 1


def test_ai_suggestion_without_decision_is_422(seeded):
    res = _client().post(
        "/orders",
        json={
            "facility_id": seeded["a"],
            "supplier_id": seeded["supplier"],
            "status": "draft",
            "source": "ai_suggestion",
            "lines": [{"ndc": NDC, "quantity": 10}],
        },
    )
    assert res.status_code == 422


def test_ai_suggestion_without_decision_rejected_by_db(seeded):
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_ck") as s:
        s.add(
            PurchaseOrder(
                ref="PO-2099-0001",
                hospital_id=HOSPITAL_A,
                facility_id=seeded["a"],
                supplier_id=seeded["supplier"],
                status="draft",
                source="ai_suggestion",
                review_decision_id=None,
                shipping=Decimal(0),
            )
        )
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()


def test_order_total_unchanged_after_catalog_price_change(seeded):
    created = _place(seeded, status="placed").json()
    original = created["total"]
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_price") as s:
        row = s.scalar(
            select(SupplierCatalog).where(SupplierCatalog.supplier_id == seeded["supplier"])
        )
        row.unit_cost = Decimal("99.0000")
    body = _client().get(f"/orders/{created['id']}").json()
    assert body["total"] == original


def test_approve_reject_and_double_approve(seeded):
    payload = {
        "ndc": NDC,
        "name": "Norepinephrine 4mg/4mL",
        "quantity": 20,
        "supplier_id": seeded["supplier"],
        "unit_cost": 11.4,
        "rationale": {"run_id": "run-1", "target_qty": 180},
    }
    rec = _client().post(
        "/recommendations",
        json={"facility_id": seeded["a"], "payload": payload},
    )
    assert rec.status_code == 201
    rec_id = rec.json()["id"]
    approved = _client().post(f"/recommendations/{rec_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "draft"
    assert approved.json()["source"] == "ai_suggestion"
    again = _client().post(f"/recommendations/{rec_id}/approve")
    assert again.status_code == 409
    listed = _client().get("/orders", params={"source": "ai_suggestion"}).json()
    assert listed["total"] == 1

    other = _client().post(
        "/recommendations",
        json={"facility_id": seeded["a"], "payload": {**payload, "rationale": {"run_id": "run-2"}}},
    ).json()
    rejected = _client().post(
        f"/recommendations/{other['id']}/reject",
        json={"reason": "stock arriving from transfer"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["decision"] == "rejected"
    assert rejected.json()["reason"] == "stock arriving from transfer"
    assert _client().get("/orders", params={"source": "ai_suggestion"}).json()["total"] == 1


def test_history_two_statuses_and_summary_definition(seeded):
    _place(seeded, status="placed")
    _place(seeded, status="draft")
    body = _client().get("/orders", params=[("status", "draft"), ("status", "placed")]).json()
    assert body["total"] == 2
    assert {row["status"] for row in body["items"]} == {"draft", "placed"}
    summary = _client().get("/orders/summary").json()
    assert summary["timezone"] == "UTC"
    assert "placed and in_transit" in summary["committed_spend"]["definition"]
    assert summary["drafts_awaiting_review"] == 1


def test_unoperated_facility_rejected(seeded):
    res = _client().post(
        "/orders",
        json={
            "facility_id": seeded["partner"],
            "supplier_id": seeded["supplier"],
            "status": "placed",
            "source": "manual",
            "lines": [{"ndc": NDC, "quantity": 10}],
        },
    )
    assert res.status_code == 422
