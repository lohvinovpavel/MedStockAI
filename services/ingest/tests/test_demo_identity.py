"""Wave 0 identity: one hospital, one facility layout, uuid hospital_id."""

from __future__ import annotations

import uuid
from pathlib import Path

from medstock_shared.db import engine
from medstock_shared.demo_tenant import (
    FACILITIES,
    HOSPITAL_ID,
    HOSPITAL_NAME,
    LEGACY_DEMO_HOSPITAL_ID,
    LOCATIONS,
    OPERATED_CODES,
    upsert_registry,
)
from medstock_shared.models import (
    AssessmentLog,
    ConsumptionDaily,
    Facility,
    ForecastPoint,
    FormularyItem,
    Hospital,
    Patient,
    StockDaily,
    StockSnapshot,
    StorageLocation,
)
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

_ROOT = Path(__file__).resolve().parents[3]
_SEED_STOCK = _ROOT / "scripts" / "seed_stock.py"

TENANT_MODELS = (
    FormularyItem,
    StockSnapshot,
    Patient,
    AssessmentLog,
    ConsumptionDaily,
    ForecastPoint,
    StockDaily,
)


def test_demo_hospital_is_st_marys_not_the_all_zeros_tenant():
    assert HOSPITAL_NAME == "St Mary's General"
    assert HOSPITAL_ID != LEGACY_DEMO_HOSPITAL_ID
    assert str(LEGACY_DEMO_HOSPITAL_ID) == "00000000-0000-0000-0000-000000000001"


def test_four_operated_sites_match_b1():
    assert OPERATED_CODES == ("central", "riverside", "westend", "warehouse-north")
    assert {f["code"] for f in FACILITIES if f["operated"]} == set(OPERATED_CODES)
    assert {f["code"] for f in FACILITIES if not f["operated"]} == {"st-luke", "mercy"}


def test_tenant_hospital_id_is_uuid_fk():
    for model in TENANT_MODELS:
        col = model.__table__.c.hospital_id
        assert isinstance(col.type, UUID), model.__tablename__
        targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "hospital.id" in targets, model.__tablename__


def test_seed_stock_uses_demo_layout_shelves_not_a_parallel_scheme():
    text = _SEED_STOCK.read_text(encoding="utf-8")
    assert "from medstock_shared.demo_tenant import" in text
    assert 'LOCATIONS = ("main-pharmacy"' not in text
    assert '"locations": ("main-pharmacy"' not in text
    assert 'location_id": "main-pharmacy"' not in text
    assert 'location_id": "icu"' not in text
    assert 'location_id": "ward-3"' not in text
    assert "fac-central" not in text
    assert "upsert_registry" in text


def test_upsert_registry_plants_four_operated_sites():
    """Acceptance: after auth seed + stock/demo seed, the hospital has the four
    switchable sites GET /warehouse/facilities?operated=true will return."""
    hid = uuid.uuid4()
    with Session(engine) as s:
        s.add(Hospital(id=hid, name=f"Wave0 {hid}"))
        s.flush()
        fac_ids, loc_ids = upsert_registry(s, hid)
        try:
            operated = {
                row.code
                for row in s.scalars(
                    select(Facility).where(Facility.hospital_id == hid, Facility.operated.is_(True))
                )
            }
            assert operated == set(OPERATED_CODES)
            assert set(fac_ids) == {f["code"] for f in FACILITIES}
            assert len(loc_ids) == sum(len(locs) for locs in LOCATIONS.values())
        finally:
            s.execute(
                delete(StorageLocation).where(StorageLocation.facility_id.in_(list(fac_ids.values())))
            )
            s.execute(delete(Facility).where(Facility.hospital_id == hid))
            s.execute(delete(Hospital).where(Hospital.id == hid))
            s.commit()

