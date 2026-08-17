"""Demo-tenant layout shared by gen_demo (writes artifacts) and seed_demo
(loads them into Postgres). One module so the two can never disagree.

Everything here is deterministic by construction: fixed anchor date (matching
web/lib/mock-data.ts "today"), fixed seed (docs/demo-data.md), fixed facility
set (docs/backend/specs/B1-facility-registry.md rule 1 — the mock's slugs).
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

DEMO_SEED = 42
# docs/demo-data.md fake-data markers: one demo tenant, unmistakably fake.
HOSPITAL_ID = "00000000-0000-0000-0000-000000000001"
HOSPITAL_NAME = "DEMO GENERAL HOSPITAL"

# The generator's "today". Fixed — a moving anchor would change every artifact
# byte on every run and defeat the committed-artifact determinism test.
END_DATE = date(2026, 8, 14)
HISTORY_DAYS = 3 * 365 + 1  # 3 years of daily consumption, ending END_DATE
CONDITION_DAYS = 90  # hourly telemetry window, ending END_DATE 23:00 UTC
CONDITION_END = datetime(2026, 8, 14, 23, 0, tzinfo=UTC)

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

# Planted scenario: one refrigerated drug shelved in a room — the excursion
# endpoint must flag it even when the room itself is within CRT range.
MISPLACED = {"facility": "westend", "query_name": "latanoprost 0.05 MG/ML Ophthalmic Solution"}


def data_dir() -> Path:
    """data/demo at the repo root; DEMO_DATA_DIR overrides (k8s Job mounts)."""
    override = os.environ.get("DEMO_DATA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data" / "demo"


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
