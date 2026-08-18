"""Warehouse API against the real schema (CI migrates, then runs these).

Fixture rows carry their own throwaway hospital so runs never collide with
seeded demo data; everything is deleted afterwards. RLS policies are still a
repo-wide open item (docs/services.md §8), so cross-tenant invisibility is not
asserted here yet — 404s are exercised via unknown ids.
"""

import uuid
from datetime import UTC, datetime

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import Principal, current_principal
from medstock_shared.db import engine
from medstock_shared.models import (
    ConsumptionDaily,
    Drug,
    Facility,
    Hospital,
    LocationCondition,
    StockSnapshot,
    StorageLocation,
)
from sqlalchemy import delete
from sqlalchemy.orm import Session

HOSPITAL_ID = uuid.UUID("00000000-0000-0000-0000-00000000c0de")
NDC_FRIDGE = "99999-test-01"
NDC_ROOM = "99999-test-02"


@pytest.fixture(scope="module")
def seeded():
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_ID, name="TEST HOSPITAL"))
        s.flush()
        central = Facility(
            hospital_id=HOSPITAL_ID, code="t-central", name="Test Central", type="Hospital",
            lat=50.45, lon=30.523, operated=True,
        )
        partner = Facility(
            hospital_id=HOSPITAL_ID, code="t-partner", name="Test Partner", type="Pharmacy",
            lat=50.6207, lon=30.523, operated=False,
        )
        s.add_all([central, partner])
        s.flush()
        room = StorageLocation(
            facility_id=central.id, code="t-room", name="Test Room", kind="room"
        )
        fridge = StorageLocation(
            facility_id=central.id, code="t-fridge", name="Test Fridge", kind="fridge"
        )
        s.add_all([room, fridge])
        s.flush()
        s.merge(Drug(
            ndc=NDC_FRIDGE, name="Testinsulin 100 UNT/ML", storage_class="refrigerated",
            storage_min_c=2.0, storage_max_c=8.0, humidity_max_pct=75.0, raw={"source": "test"},
        ))
        s.merge(Drug(
            ndc=NDC_ROOM, name="Testformin 500 MG Oral Tablet", storage_class="crt",
            storage_min_c=15.0, storage_max_c=25.0, humidity_max_pct=60.0, raw={"source": "test"},
        ))
        s.add_all([
            StockSnapshot(
                hospital_id=HOSPITAL_ID, ndc=NDC_FRIDGE, facility_id=central.id,
                location_id="t-fridge", quantity=40,
            ),
            StockSnapshot(
                hospital_id=HOSPITAL_ID, ndc=NDC_ROOM, facility_id=central.id,
                location_id="t-room", quantity=500,
            ),
        ])
        s.add_all([
            ConsumptionDaily(
                hospital_id=HOSPITAL_ID, facility_id=central.id, ndc=NDC_ROOM,
                rxcui="000000", date=f"2026-08-{day:02d}", qty_consumed=10 + day,
                stockout=(day == 3),
            )
            for day in range(1, 11)
        ])
        # Fridge healthy for 3 hours, then a 3-hour warm excursion (>8 °C).
        readings = [4.5, 4.7, 4.4, 11.0, 12.5, 13.0]
        s.add_all([
            LocationCondition(
                location_id=fridge.id, ts=datetime(2026, 8, 3, 20 + i % 4, i // 4, tzinfo=UTC),
                temperature_c=temp, humidity_pct=55.0,
            )
            for i, temp in enumerate(readings)
        ])
        # Room stays in range — must produce no excursion.
        s.add(LocationCondition(
            location_id=room.id, ts=datetime(2026, 8, 3, 20, tzinfo=UTC),
            temperature_c=21.0, humidity_pct=45.0,
        ))
        s.commit()
        ids = {"central": central.id, "partner": partner.id, "room": room.id, "fridge": fridge.id}

    yield ids

    with Session(engine) as s:
        s.execute(delete(ConsumptionDaily).where(ConsumptionDaily.hospital_id == HOSPITAL_ID))
        s.execute(delete(StockSnapshot).where(StockSnapshot.hospital_id == HOSPITAL_ID))
        s.execute(delete(LocationCondition).where(
            LocationCondition.location_id.in_([ids["room"], ids["fridge"]])
        ))
        s.execute(delete(StorageLocation).where(
            StorageLocation.facility_id.in_([ids["central"], ids["partner"]])
        ))
        s.execute(delete(Facility).where(Facility.hospital_id == HOSPITAL_ID))
        s.execute(delete(Drug).where(Drug.ndc.in_([NDC_FRIDGE, NDC_ROOM])))
        s.execute(delete(Hospital).where(Hospital.id == HOSPITAL_ID))
        s.commit()


def _client(role: str = "pharmacist") -> TestClient:
    app.dependency_overrides[current_principal] = lambda: Principal(
        "user-t", str(HOSPITAL_ID), role
    )
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_facilities_requires_auth():
    assert TestClient(app).get("/facilities").status_code == 401


def test_facilities_rejects_unknown_role(seeded):
    assert _client(role="ghost").get("/facilities").status_code == 403


def test_facilities_lists_and_filters_operated(seeded):
    items = _client().get("/facilities").json()["items"]
    codes = {f["code"] for f in items}
    assert {"t-central", "t-partner"} <= codes
    operated = _client().get("/facilities", params={"operated": "true"}).json()["items"]
    assert "t-partner" not in {f["code"] for f in operated}


def test_facility_detail_computes_distance(seeded):
    body = _client().get(
        f"/facilities/{seeded['partner']}", params={"from": seeded["central"]}
    ).json()
    assert body["code"] == "t-partner"
    assert body["distance_km_from"] == pytest.approx(19.0, abs=0.2)
    same = _client().get(
        f"/facilities/{seeded['central']}", params={"from": seeded["central"]}
    ).json()
    assert same["distance_km_from"] == 0


def test_facility_404(seeded):
    assert _client().get("/facilities/999999999").status_code == 404


def test_locations_by_facility(seeded):
    items = _client().get("/locations", params={"facility_id": seeded["central"]}).json()["items"]
    assert [(i["code"], i["kind"]) for i in items] == [("t-room", "room"), ("t-fridge", "fridge")]
    assert _client().get("/locations", params={"facility_id": 999999999}).status_code == 404


def test_stock_joins_drug_metadata(seeded):
    items = _client().get("/stock", params={"facility_id": seeded["central"]}).json()["items"]
    by_ndc = {i["ndc"]: i for i in items}
    assert by_ndc[NDC_FRIDGE]["storage_class"] == "refrigerated"
    assert by_ndc[NDC_FRIDGE]["location"] == "t-fridge"
    assert by_ndc[NDC_ROOM]["quantity"] == 500


def test_consumption_series_and_date_window(seeded):
    body = _client().get(
        "/consumption",
        params={"ndc": NDC_ROOM, "facility_id": seeded["central"], "from": "2026-08-03", "to": "2026-08-05"},
    ).json()
    assert body["rxcui"] == "000000"
    assert [i["date"] for i in body["items"]] == ["2026-08-03", "2026-08-04", "2026-08-05"]
    assert body["items"][0]["stockout"] is True
    assert body["items"][1]["stockout"] is False


def test_conditions_window_and_404(seeded):
    body = _client().get(f"/locations/{seeded['fridge']}/conditions").json()
    assert body["location"]["kind"] == "fridge"
    assert len(body["items"]) == 6
    assert _client().get("/locations/999999999/conditions").status_code == 404


def test_excursions_flag_exactly_the_planted_breach(seeded):
    items = _client().get(
        "/excursions", params={"facility_id": seeded["central"]}
    ).json()["items"]
    ours = [i for i in items if i["ndc"] in (NDC_FRIDGE, NDC_ROOM)]
    assert len(ours) == 1
    hit = ours[0]
    assert hit["ndc"] == NDC_FRIDGE
    assert hit["location"] == "t-fridge"
    assert hit["violations"] == ["temperature"]
    assert hit["hours"] == 3  # only the three >8 °C readings
    assert hit["observed_max_c"] == pytest.approx(13.0)
    assert hit["quantity"] == 40


def test_ingress_prefix_mount(seeded):
    assert _client().get(
        "/api/warehouse/facilities", params={"operated": "true"}
    ).status_code == 200
