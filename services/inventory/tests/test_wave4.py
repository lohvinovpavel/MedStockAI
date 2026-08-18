"""Wave 4 G1: live shortage matrix from shortage_event + stock + E2 trailing mean."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import Principal, current_principal
from medstock_shared.db import engine, session_scope
from medstock_shared.models import (
    ConsumptionDaily,
    Drug,
    Facility,
    FormularyItem,
    Hospital,
    ParLevel,
    ShortageEvent,
    StockBatch,
    StockSnapshot,
)
from medstock_shared.stock import coverage_band, days_of_supply_from_mean
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

HOSPITAL_A = uuid.UUID("00000000-0000-0000-0000-00000000a1a1")
HOSPITAL_B = uuid.UUID("00000000-0000-0000-0000-00000000a1b2")
ACTOR = uuid.UUID("00000000-0000-0000-0000-00000000a1c3")
NDC = "00338011220"
RXCUI = "1049640"
SOURCE_ID = "FDA-2026-0142-g1"
OTHER_SOURCE = "FDA-9999-0001-g1"
OTHER_NDC = "99999000002"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_principal, None)


def _wipe(hospital_id: uuid.UUID) -> None:
    with session_scope(str(hospital_id), str(ACTOR), "test_teardown") as s:
        s.execute(delete(ConsumptionDaily).where(ConsumptionDaily.hospital_id == hospital_id))
        s.execute(delete(ParLevel).where(ParLevel.hospital_id == hospital_id))
        s.execute(delete(StockBatch).where(StockBatch.hospital_id == hospital_id))
        s.execute(delete(StockSnapshot).where(StockSnapshot.hospital_id == hospital_id))
        s.execute(delete(FormularyItem).where(FormularyItem.hospital_id == hospital_id))
        s.execute(delete(Facility).where(Facility.hospital_id == hospital_id))


def _client(role: str = "pharmacist", hospital_id: uuid.UUID = HOSPITAL_A) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: Principal(
        str(ACTOR), str(hospital_id), role
    )
    return TestClient(app)


def _plant_consumption(hospital_id: uuid.UUID, facility_id: int, ndc: str, daily: int) -> None:
    today = datetime.now(tz=UTC).date()
    with session_scope(str(hospital_id), str(ACTOR), "test_g1") as s:
        s.execute(
            delete(ConsumptionDaily).where(
                ConsumptionDaily.hospital_id == hospital_id,
                ConsumptionDaily.facility_id == facility_id,
                ConsumptionDaily.ndc == ndc,
            )
        )
        s.add_all(
            [
                ConsumptionDaily(
                    hospital_id=hospital_id,
                    facility_id=facility_id,
                    ndc=ndc,
                    rxcui=RXCUI,
                    date=today - timedelta(days=offset),
                    qty_consumed=daily,
                    stockout=False,
                )
                for offset in range(28)
            ]
        )


def _plant_stock(
    hospital_id: uuid.UUID,
    facility_id: int,
    ndc: str,
    qty: int,
    *,
    reorder: int = 80,
    target: int = 200,
    location: str = "main-room",
) -> None:
    with session_scope(str(hospital_id), str(ACTOR), "test_g1") as s:
        s.add(
            StockBatch(
                hospital_id=hospital_id,
                facility_id=facility_id,
                ndc=ndc,
                lot=f"G1-{ndc[-4:]}-{facility_id}",
                expiry_date=datetime.now(tz=UTC).date() + timedelta(days=30),
                quantity=qty,
                location_id=location,
            )
        )
        s.add(
            ParLevel(
                hospital_id=hospital_id,
                facility_id=facility_id,
                ndc=ndc,
                reorder_point=reorder,
                target_qty=target,
            )
        )


@pytest.fixture
def seeded():
    _wipe(HOSPITAL_A)
    _wipe(HOSPITAL_B)
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_A, name="WAVE4 G1 HOSPITAL A"))
        s.merge(Hospital(id=HOSPITAL_B, name="WAVE4 G1 HOSPITAL B"))
        s.flush()
        central = Facility(
            hospital_id=HOSPITAL_A, code="g1-central", name="G1 Central",
            type="Hospital", lat=50.45, lon=30.523, operated=True,
        )
        warehouse = Facility(
            hospital_id=HOSPITAL_A, code="g1-wh", name="G1 Warehouse",
            type="Warehouse", lat=50.7554, lon=30.523, operated=True,
        )
        partner = Facility(
            hospital_id=HOSPITAL_A, code="g1-luke", name="G1 St Luke",
            type="Hospital", lat=50.45, lon=30.6922, operated=False,
        )
        other = Facility(
            hospital_id=HOSPITAL_B, code="g1-b", name="G1 B",
            type="Hospital", lat=50.45, lon=30.523, operated=True,
        )
        s.add_all([central, warehouse, partner, other])
        s.flush()
        existing = s.scalar(select(Drug).where(Drug.ndc == NDC))
        if existing is None:
            s.add(Drug(ndc=NDC, name="Norepinephrine 4mg/4mL", raw={"source": "test", "rxcui": RXCUI}))
        else:
            existing.name = "Norepinephrine 4mg/4mL"
            existing.raw = {**(existing.raw or {}), "rxcui": RXCUI}
        extra = s.scalar(select(Drug).where(Drug.ndc == OTHER_NDC))
        if extra is None:
            s.add(Drug(ndc=OTHER_NDC, name="Unrelated", raw={"source": "test"}))
        s.execute(
            insert(ShortageEvent)
            .values(
                source_id=SOURCE_ID,
                ndc=NDC,
                status="Currently in Shortage",
                raw={"note": "G1 test", "name": "Norepinephrine 4mg/4mL", "agency": "FDA", "rxcui": RXCUI},
            )
            .on_conflict_do_update(
                index_elements=["source_id"],
                set_={"ndc": NDC, "status": "Currently in Shortage"},
            )
        )
        s.execute(
            insert(ShortageEvent)
            .values(
                source_id=OTHER_SOURCE,
                ndc=OTHER_NDC,
                status="Currently in Shortage",
                raw={"note": "not on this formulary", "name": "Unrelated"},
            )
            .on_conflict_do_update(
                index_elements=["source_id"],
                set_={"ndc": OTHER_NDC, "status": "Currently in Shortage"},
            )
        )
        s.commit()
        ids = {"central": central.id, "warehouse": warehouse.id, "partner": partner.id, "b": other.id}
    yield ids
    _wipe(HOSPITAL_A)
    _wipe(HOSPITAL_B)
    with Session(engine) as s:
        s.execute(delete(ShortageEvent).where(ShortageEvent.source_id.in_([SOURCE_ID, OTHER_SOURCE])))
        s.commit()


def test_coverage_band_table():
    assert coverage_band(0, 10) == "stockout"
    assert coverage_band(10, 5) == "critical"
    assert coverage_band(10, 3.5) == "critical"
    assert coverage_band(100, 60) == "surplus"
    assert coverage_band(100, 14) == "normal"
    assert coverage_band(10, None) == "normal"
    assert days_of_supply_from_mean(100, 10) == 10
    assert days_of_supply_from_mean(0, 10) == 0.0
    assert days_of_supply_from_mean(10, None) is None


def test_unrelated_shortage_hidden(seeded, monkeypatch):
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: [NDC] if rxcui == RXCUI else [])
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_g1") as s:
        s.add(FormularyItem(hospital_id=HOSPITAL_A, rxcui=RXCUI))
    _plant_stock(HOSPITAL_A, seeded["central"], NDC, 12, reorder=80, target=200)
    items = _client().get("/shortages", params={"facility_id": seeded["central"]}).json()["items"]
    ids = {row["id"] for row in items}
    assert SOURCE_ID in ids
    assert OTHER_SOURCE not in ids


def test_tenant_without_stock_or_formulary_sees_nothing(seeded):
    body = _client("pharmacist", HOSPITAL_B).get(
        "/shortages", params={"facility_id": seeded["b"]}
    ).json()
    assert body["items"] == []
    res = _client("pharmacist", HOSPITAL_B).get(
        f"/shortages/{SOURCE_ID}/coverage", params={"facility_id": seeded["b"]}
    )
    assert res.status_code == 404


def test_coverage_matches_items_quantity_and_stockout(seeded, monkeypatch):
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: [NDC] if rxcui == RXCUI else [])
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_g1") as s:
        s.add(FormularyItem(hospital_id=HOSPITAL_A, rxcui=RXCUI))
    _plant_stock(HOSPITAL_A, seeded["central"], NDC, 0, reorder=80, target=200)
    _plant_stock(HOSPITAL_A, seeded["warehouse"], NDC, 700, reorder=80, target=200)
    _plant_consumption(HOSPITAL_A, seeded["warehouse"], NDC, daily=10)
    c = _client()
    items = c.get("/items", params={"facility_id": seeded["central"]}).json()["items"]
    assert items[0]["quantity"] == 0
    assert items[0]["status"] == "stockout"
    cov = c.get(
        f"/shortages/{SOURCE_ID}/coverage", params={"facility_id": seeded["central"]}
    ).json()
    assert cov["viewing_from"] == seeded["central"]
    by_code = {row["facility"]["code"]: row for row in cov["rows"]}
    assert by_code["g1-central"]["quantity"] == 0
    assert by_code["g1-central"]["coverage"] == "stockout"
    assert by_code["g1-central"]["is_current"] is True
    assert by_code["g1-central"]["distance_km"] == 0
    assert by_code["g1-wh"]["quantity"] == 700
    assert by_code["g1-wh"]["coverage"] == "surplus"
    warehouse_items = c.get("/items", params={"facility_id": seeded["warehouse"]}).json()["items"]
    assert warehouse_items[0]["quantity"] == 700
    assert warehouse_items[0]["status"] == "surplus"
    assert by_code["g1-wh"]["coverage"] == warehouse_items[0]["status"]


def test_partner_appears_and_is_not_operated(seeded, monkeypatch):
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: [NDC] if rxcui == RXCUI else [])
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_g1") as s:
        s.add(FormularyItem(hospital_id=HOSPITAL_A, rxcui=RXCUI))
    _plant_stock(HOSPITAL_A, seeded["central"], NDC, 12, reorder=80, target=200)
    _plant_stock(HOSPITAL_A, seeded["partner"], NDC, 210, reorder=80, target=200)
    _plant_consumption(HOSPITAL_A, seeded["partner"], NDC, daily=3)
    cov = _client().get(
        f"/shortages/{SOURCE_ID}/coverage", params={"facility_id": seeded["central"]}
    ).json()
    partner = next(row for row in cov["rows"] if row["facility"]["code"] == "g1-luke")
    assert partner["facility"]["operated"] is False
    assert partner["quantity"] == 210
    assert partner["coverage"] == "surplus"
    assert partner["is_current"] is False
    assert partner["distance_km"] == pytest.approx(12.0, abs=0.3)


def test_network_surplus_count_and_sort(seeded, monkeypatch):
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: [NDC] if rxcui == RXCUI else [])
    with session_scope(str(HOSPITAL_A), str(ACTOR), "test_g1") as s:
        s.add(FormularyItem(hospital_id=HOSPITAL_A, rxcui=RXCUI))
    _plant_stock(HOSPITAL_A, seeded["central"], NDC, 10, reorder=80, target=200)
    _plant_consumption(HOSPITAL_A, seeded["central"], NDC, daily=10)
    _plant_stock(HOSPITAL_A, seeded["warehouse"], NDC, 700, reorder=80, target=200)
    _plant_consumption(HOSPITAL_A, seeded["warehouse"], NDC, daily=10)
    c = _client()
    alerts = c.get("/shortages", params={"facility_id": seeded["central"]}).json()["items"]
    story = next(row for row in alerts if row["id"] == SOURCE_ID)
    assert story["ndc"] == NDC
    assert story["source"] == "FDA"
    assert story["network"]["surplus_facilities"] == 1
    cov = c.get(
        f"/shortages/{SOURCE_ID}/coverage", params={"facility_id": seeded["central"]}
    ).json()
    surplus = [row for row in cov["rows"] if row["coverage"] == "surplus"]
    assert story["network"]["surplus_facilities"] == len(surplus)
    ranks = [row["coverage"] for row in cov["rows"]]
    order = {"surplus": 0, "normal": 1, "critical": 2, "stockout": 3}
    assert ranks == sorted(ranks, key=lambda band: order[band])
