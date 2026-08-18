"""The one demo hospital every seeder writes into.

Auth (`ann@stmarys.org`), seed_demo, seed_stock and seed_patients used to
disagree: St Mary's General got a random UUID, while seed_demo inserted
DEMO GENERAL HOSPITAL at the all-zeros id. They are the same tenant now —
resolve by name, and only mint `HOSPITAL_ID` when the row does not exist.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import Facility, Hospital, StorageLocation

HOSPITAL_NAME = "St Mary's General"
# Deterministic so a brand-new environment is reproducible. Seeders that find
# an existing row by name keep that id — they never create a second hospital.
HOSPITAL_ID = uuid.uuid5(uuid.NAMESPACE_URL, "https://medstock.ai/demo/st-marys-general")
LEGACY_DEMO_HOSPITAL_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
LEGACY_DEMO_HOSPITAL_NAME = "DEMO GENERAL HOSPITAL"

# B1 rule 1: codes are the mock's slugs so the web app migrates without a
# translation table. lat/lon are placed so haversine reproduces the mock's
# distanceKm from central (0 / 19 / 41 / 34 / 12 / 27 km).
# `scale` sizes consumption relative to central; None = partner site, no
# consumption history (their stock only feeds the shortage matrix).
FACILITIES: list[dict] = [
    {"code": "central", "name": "Central Hospital", "type": "Hospital",
     "lat": 50.450000, "lon": 30.523000, "operated": True, "scale": 1.0},
    {"code": "riverside", "name": "Riverside Outpatient", "type": "Clinic",
     "lat": 50.620700, "lon": 30.523000, "operated": True, "scale": 0.35},
    {"code": "westend", "name": "West End Community", "type": "Clinic",
     "lat": 50.450000, "lon": 29.945000, "operated": True, "scale": 0.30},
    {"code": "warehouse-north", "name": "Regional Warehouse North", "type": "Warehouse",
     "lat": 50.755400, "lon": 30.523000, "operated": True, "scale": 0.60},
    {"code": "st-luke", "name": "St. Luke Hospital", "type": "Hospital",
     "lat": 50.450000, "lon": 30.692200, "operated": False, "scale": None},
    {"code": "mercy", "name": "Mercy Pharmacy Network", "type": "Pharmacy",
     "lat": 50.450000, "lon": 30.142300, "operated": False, "scale": None},
]

# (code, name, kind) per facility. Kinds drive both condition simulation and
# storage-class placement. warehouse-north's bulk hall is deliberately the one
# non-climate-controlled room — its summer drift is a planted excursion.
LOCATIONS: dict[str, list[tuple[str, str, str]]] = {
    "central": [
        ("main-room", "Main Pharmacy Room", "room"),
        ("fridge-1", "Pharmacy Fridge 1", "fridge"),
        ("freezer-1", "Vaccine Freezer", "freezer"),
    ],
    "riverside": [
        ("main-room", "Dispensary Room", "room"),
        ("fridge-1", "Dispensary Fridge", "fridge"),
    ],
    "westend": [
        ("main-room", "Dispensary Room", "room"),
        ("fridge-1", "Dispensary Fridge", "fridge"),
    ],
    "warehouse-north": [
        ("bulk-room", "Bulk Storage Hall", "room"),
        ("cold-room-1", "Cold Room", "cold_room"),
        ("freezer-1", "Deep Freezer", "freezer"),
    ],
    "st-luke": [
        ("main-room", "Main Pharmacy Room", "room"),
        ("fridge-1", "Pharmacy Fridge", "fridge"),
    ],
    "mercy": [
        ("main-room", "Dispensary Room", "room"),
        ("fridge-1", "Dispensary Fridge", "fridge"),
    ],
}

# storage_class → acceptable location kinds, first existing kind wins.
CLASS_TO_KINDS: dict[str, tuple[str, ...]] = {
    "crt": ("room",),
    "refrigerated": ("fridge", "cold_room"),
    "freezer": ("freezer",),
}

OPERATED_CODES: tuple[str, ...] = tuple(f["code"] for f in FACILITIES if f["operated"])


def location_for(facility_code: str, storage_class: str) -> str | None:
    """Location code where a drug of this class is shelved at this facility,
    or None when the facility can't store it (e.g. freezer drugs at clinics).
    """
    wanted = CLASS_TO_KINDS[storage_class]
    for kind in wanted:
        for code, _name, loc_kind in LOCATIONS[facility_code]:
            if loc_kind == kind:
                return code
    return None


def resolve_or_create_hospital(session: Session) -> uuid.UUID:
    """The live demo tenant. Finds St Mary's by name; inserts `HOSPITAL_ID`
    only when the row is missing. Never creates DEMO GENERAL HOSPITAL.
    """
    row = session.execute(
        select(Hospital).where(Hospital.name == HOSPITAL_NAME)
    ).scalars().first()
    if row is not None:
        return row.id
    session.execute(insert(Hospital).values(id=HOSPITAL_ID, name=HOSPITAL_NAME))
    session.flush()
    return HOSPITAL_ID


def upsert_registry(
    session: Session, hospital_id: uuid.UUID
) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    """Facilities and storage locations for this hospital; returns
    (facility-code → id, (facility-code, location-code) → id).
    """
    for fac in FACILITIES:
        values = {
            "hospital_id": hospital_id,
            "code": fac["code"],
            "name": fac["name"],
            "type": fac["type"],
            "lat": fac["lat"],
            "lon": fac["lon"],
            "operated": fac["operated"],
        }
        session.execute(
            insert(Facility)
            .values(**values)
            .on_conflict_do_update(index_elements=["hospital_id", "code"], set_=values)
        )
    fac_ids = {
        code: fid
        for fid, code in session.execute(
            select(Facility.id, Facility.code).where(Facility.hospital_id == hospital_id)
        )
    }
    for fac_code, locations in LOCATIONS.items():
        for code, name, kind in locations:
            values = {
                "facility_id": fac_ids[fac_code],
                "code": code,
                "name": name,
                "kind": kind,
            }
            session.execute(
                insert(StorageLocation)
                .values(**values)
                .on_conflict_do_update(index_elements=["facility_id", "code"], set_=values)
            )
    loc_ids: dict[tuple[str, str], int] = {}
    for fac_code, fid in fac_ids.items():
        for lid, code in session.execute(
            select(StorageLocation.id, StorageLocation.code).where(
                StorageLocation.facility_id == fid
            )
        ):
            loc_ids[(fac_code, code)] = lid
    return fac_ids, loc_ids
