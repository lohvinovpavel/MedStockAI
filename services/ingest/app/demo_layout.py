"""Demo-tenant layout shared by gen_demo (writes artifacts) and seed_demo
(loads them into Postgres). One module so the two can never disagree.

Hospital identity and the facility/location registry live in
`medstock_shared.demo_tenant` so auth, seed_stock and seed_patients cannot
drift from this file. Dates and the planted MISPLACED scenario stay here —
they belong to the generator, not to login.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

from medstock_shared.demo_tenant import (
    CLASS_TO_KINDS,
    FACILITIES,
    HOSPITAL_ID,
    HOSPITAL_NAME,
    LOCATIONS,
    OPERATED_CODES,
    location_for,
    resolve_or_create_hospital,
    upsert_registry,
)

__all__ = [
    "CLASS_TO_KINDS",
    "CONDITION_DAYS",
    "CONDITION_END",
    "DEMO_SEED",
    "END_DATE",
    "FACILITIES",
    "HISTORY_DAYS",
    "HOSPITAL_ID",
    "HOSPITAL_NAME",
    "LOCATIONS",
    "MISPLACED",
    "OPERATED_CODES",
    "data_dir",
    "location_for",
    "resolve_or_create_hospital",
    "upsert_registry",
]

DEMO_SEED = 42

# The generator's "today". Fixed — a moving anchor would change every artifact
# byte on every run and defeat the committed-artifact determinism test.
END_DATE = date(2026, 8, 14)
HISTORY_DAYS = 3 * 365 + 1  # 3 years of daily consumption, ending END_DATE
CONDITION_DAYS = 90  # hourly telemetry window, ending END_DATE 23:00 UTC
CONDITION_END = datetime(2026, 8, 14, 23, 0, tzinfo=UTC)

# Planted scenario: one refrigerated drug shelved in a room — the excursion
# endpoint must flag it even when the room itself is within CRT range.
MISPLACED = {"facility": "westend", "query_name": "latanoprost 0.05 MG/ML Ophthalmic Solution"}


def data_dir() -> Path:
    """data/demo at the repo root; DEMO_DATA_DIR overrides (k8s Job mounts)."""
    override = os.environ.get("DEMO_DATA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data" / "demo"
