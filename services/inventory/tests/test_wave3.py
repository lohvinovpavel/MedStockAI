"""Wave 3: B6 formulary import and B3 exposure query."""

from __future__ import annotations

import io
import uuid
from datetime import date, timedelta

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import PERMS, Principal, current_principal
from medstock_shared.db import engine, session_scope
from medstock_shared.formulary import parse_formulary_csv
from medstock_shared.models import (
    Drug,
    Facility,
    FormularyItem,
    Hospital,
    ParLevel,
    ShortageEvent,
    StockBatch,
    StockSnapshot,
)
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

HOSPITAL_A = uuid.UUID("00000000-0000-0000-0000-00000000b3a1")
HOSPITAL_B = uuid.UUID("00000000-0000-0000-0000-00000000b3b2")
ACTOR = uuid.UUID("00000000-0000-0000-0000-00000000b3c3")
NDC_STORY = "00338011220"
NDC_OTHER = "99999000001"
RXCUI_STORY = "1049640"
RXCUI_EMPTY = "9999999"
SOURCE_ID = "FDA-2026-0142-test"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_principal, None)


def _wipe(hospital_id: uuid.UUID) -> None:
    with session_scope(str(hospital_id), str(ACTOR), "test_teardown") as s:
        s.execute(delete(ParLevel).where(ParLevel.hospital_id == hospital_id))
        s.execute(delete(StockBatch).where(StockBatch.hospital_id == hospital_id))
        s.execute(delete(StockSnapshot).where(StockSnapshot.hospital_id == hospital_id))
        s.execute(delete(FormularyItem).where(FormularyItem.hospital_id == hospital_id))
        s.execute(delete(Facility).where(Facility.hospital_id == hospital_id))


@pytest.fixture
def seeded():
    _wipe(HOSPITAL_A)
    _wipe(HOSPITAL_B)
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_A, name="WAVE3 HOSPITAL A"))
        s.merge(Hospital(id=HOSPITAL_B, name="WAVE3 HOSPITAL B"))
        s.flush()
        fa = Facility(
            hospital_id=HOSPITAL_A, code="w3-central", name="W3 Central",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        fb = Facility(
            hospital_id=HOSPITAL_B, code="w3-b", name="W3 B",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        s.add_all([fa, fb])
        s.flush()
        existing = s.scalar(select(Drug).where(Drug.ndc == NDC_STORY))
        if existing is None:
            s.add(Drug(ndc=NDC_STORY, name="Norepinephrine 4mg/4mL", raw={"source": "test", "rxcui": RXCUI_STORY}))
        else:
            existing.name = "Norepinephrine 4mg/4mL"
            existing.raw = {**(existing.raw or {}), "source": "test", "rxcui": RXCUI_STORY}
        extra = s.scalar(select(Drug).where(Drug.ndc == NDC_OTHER))
        if extra is None:
            s.add(Drug(ndc=NDC_OTHER, name="Not On Formulary", raw={"source": "test"}))
        s.execute(
            insert(ShortageEvent)
            .values(
                source_id=SOURCE_ID,
                ndc=NDC_STORY,
                status="Currently in Shortage",
                raw={"test": True},
            )
            .on_conflict_do_update(
                index_elements=["source_id"],
                set_={"ndc": NDC_STORY, "status": "Currently in Shortage"},
            )
        )
        s.commit()
        ids = {"a": fa.id, "b": fb.id}
    yield ids
    _wipe(HOSPITAL_A)
    _wipe(HOSPITAL_B)
    with Session(engine) as s:
        s.execute(delete(ShortageEvent).where(ShortageEvent.source_id == SOURCE_ID))
        s.commit()


def _client(role: str = "admin", hospital_id: uuid.UUID = HOSPITAL_A) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: Principal(
        str(ACTOR), str(hospital_id), role
    )
    return TestClient(app)


def test_admin_holds_formulary_write():
    assert "formulary:write" in PERMS["admin"]
    assert "formulary:write" not in PERMS["pharmacist"]


def test_parse_keeps_first_duplicate_and_reports_bad_rows():
    text = "rxcui,name\n1049640,norepi\nabc,bad\n1049640,again\n\n246461,aspirin\n"
    rxcuis, rejected = parse_formulary_csv(text)
    assert rxcuis == ["1049640", "246461"]
    reasons = {r["reason"] for r in rejected}
    assert "rxcui_not_numeric" in reasons
    assert "duplicate_in_file" in reasons
    assert "blank_line" in reasons


def test_parse_rejects_unrecognised_header():
    with pytest.raises(ValueError, match="unrecognised_header"):
        parse_formulary_csv("drug,name\n1,x\n")


def test_pharmacist_cannot_import():
    csv_body = b"rxcui,name\n1049640,norepinephrine\n"
    res = _client("pharmacist").post(
        "/formulary/import",
        files={"file": ("f.csv", io.BytesIO(csv_body), "text/csv")},
    )
    assert res.status_code == 403


def test_import_is_additive_and_idempotent(seeded):
    csv_body = b"rxcui,name\n1049640,norepinephrine 1 MG/ML\n"
    c = _client()
    first = c.post("/formulary/import", files={"file": ("f.csv", io.BytesIO(csv_body), "text/csv")})
    assert first.status_code == 200, first.text
    assert first.json()["inserted"] == 1
    assert first.json()["updated"] == 0
    listed = c.get("/formulary").json()["items"]
    assert {row["rxcui"] for row in listed} == {RXCUI_STORY}
    second = c.post("/formulary/import", files={"file": ("f.csv", io.BytesIO(csv_body), "text/csv")})
    assert second.json()["inserted"] == 0
    assert second.json()["updated"] == 1
    assert c.get("/formulary").json()["total"] == 1


def test_bad_row_imports_the_rest(seeded):
    csv_body = b"rxcui,name\n1049640,ok\nabc,bad\n246461,aspirin\n"
    res = _client().post("/formulary/import", files={"file": ("f.csv", io.BytesIO(csv_body), "text/csv")})
    assert res.status_code == 200
    body = res.json()
    assert body["inserted"] == 2
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["reason"] == "rxcui_not_numeric"
    assert {row["rxcui"] for row in _client().get("/formulary").json()["items"]} == {
        "1049640",
        "246461",
    }


def test_six_mb_upload_rejected_before_parse(seeded):
    payload = b"rxcui\n" + b"1" * (5 * 1024 * 1024 + 10)
    res = _client().post(
        "/formulary/import",
        files={"file": ("big.csv", io.BytesIO(payload), "text/csv")},
    )
    assert res.status_code == 422
    assert res.json()["detail"] == "file_too_large"


def test_too_many_rows_is_422(seeded):
    lines = ["rxcui"] + [str(i) for i in range(10_001)]
    payload = ("\n".join(lines) + "\n").encode()
    res = _client().post(
        "/formulary/import",
        files={"file": ("many.csv", io.BytesIO(payload), "text/csv")},
    )
    assert res.status_code == 422
    assert res.json()["detail"] == "too_many_rows"


def test_delete_formulary_item(seeded):
    csv_body = b"rxcui\n1049640\n"
    c = _client()
    c.post("/formulary/import", files={"file": ("f.csv", io.BytesIO(csv_body), "text/csv")})
    res = c.delete(f"/formulary/{RXCUI_STORY}")
    assert res.status_code == 204
    assert c.get("/formulary").json()["items"] == []


def _plant_stock(facility_id: int, hospital_id: uuid.UUID, ndc: str, qty: int, reorder: int, target: int) -> None:
    with session_scope(str(hospital_id), str(ACTOR), "test_wave3") as s:
        s.add(
            StockBatch(
                hospital_id=hospital_id, facility_id=facility_id, ndc=ndc,
                lot=f"W3-{ndc[-4:]}", expiry_date=date.today() + timedelta(days=30),
                quantity=qty, location_id="main-room",
            )
        )
        s.add(
            ParLevel(
                hospital_id=hospital_id, facility_id=facility_id, ndc=ndc,
                reorder_point=reorder, target_qty=target,
            )
        )


def test_exposure_shortage_and_uncovered(seeded, monkeypatch):
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: [NDC_STORY] if rxcui == RXCUI_STORY else [])
    _plant_stock(seeded["a"], HOSPITAL_A, NDC_STORY, qty=6, reorder=80, target=200)
    c = _client()
    c.post("/formulary/import", files={"file": ("f.csv", io.BytesIO(b"rxcui\n1049640\n9999999\n"), "text/csv")})
    # Stocked SKU that is not on the formulary must not appear.
    _plant_stock(seeded["a"], HOSPITAL_A, NDC_OTHER, qty=400, reorder=10, target=50)

    body = c.get("/exposure", params={"facility_id": seeded["a"]}).json()
    assert body["uncovered_rule"] == "below_par"
    rxcuis = {row["rxcui"] for row in body["items"]}
    assert RXCUI_STORY in rxcuis
    assert RXCUI_EMPTY in rxcuis
    ndcs = {row["ndc"] for row in body["items"]}
    assert NDC_OTHER not in ndcs
    story = next(row for row in body["items"] if row["rxcui"] == RXCUI_STORY)
    assert story["shortage_source_id"] == SOURCE_ID
    assert story["quantity"] == 6
    empty = next(row for row in body["items"] if row["rxcui"] == RXCUI_EMPTY)
    assert empty["quantity"] == 0
    assert body["totals"]["formulary_skus"] == 2
    assert body["totals"]["in_shortage"] == 1
    assert body["totals"]["uncovered"] == 1
    # Totals are not a Python len(items) count of a filtered array — two
    # formulary SKUs, one item row of which has no NDC.
    assert body["totals"]["formulary_skus"] == 2


def test_two_tenants_share_shortage_not_quantity(seeded, monkeypatch):
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: [NDC_STORY] if rxcui == RXCUI_STORY else [])
    _plant_stock(seeded["a"], HOSPITAL_A, NDC_STORY, qty=6, reorder=80, target=200)
    _plant_stock(seeded["b"], HOSPITAL_B, NDC_STORY, qty=400, reorder=80, target=200)
    csv_body = b"rxcui\n1049640\n"
    _client("admin", HOSPITAL_A).post(
        "/formulary/import", files={"file": ("f.csv", io.BytesIO(csv_body), "text/csv")}
    )
    _client("admin", HOSPITAL_B).post(
        "/formulary/import", files={"file": ("f.csv", io.BytesIO(csv_body), "text/csv")}
    )
    a = _client("admin", HOSPITAL_A).get("/exposure", params={"facility_id": seeded["a"]}).json()
    b = _client("admin", HOSPITAL_B).get("/exposure", params={"facility_id": seeded["b"]}).json()
    assert a["items"][0]["shortage_source_id"] == b["items"][0]["shortage_source_id"] == SOURCE_ID
    assert a["items"][0]["quantity"] == 6
    assert b["items"][0]["quantity"] == 400
    assert a["totals"]["uncovered"] == 1
    assert b["totals"]["uncovered"] == 0


def test_items_in_formulary_true_after_import(seeded, monkeypatch):
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: [NDC_STORY])
    _plant_stock(seeded["a"], HOSPITAL_A, NDC_STORY, qty=6, reorder=80, target=200)
    _client().post(
        "/formulary/import",
        files={"file": ("f.csv", io.BytesIO(b"rxcui\n1049640\n"), "text/csv")},
    )
    items = _client("pharmacist").get("/items", params={"facility_id": seeded["a"]}).json()["items"]
    assert items[0]["in_formulary"] is True
