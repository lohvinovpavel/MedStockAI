"""Seed the demo tenant from the committed data/demo artifacts (issue #8).

    ENVIRONMENT=demo python -m app.seed_demo

Loads what gen_demo generated — nothing is computed here, so seeding is fast
and reruns are idempotent (upserts for reference/registry rows; delete-and-
reload for the two bulk time series, scoped to the demo tenant only).

Refuses to run unless ENVIRONMENT=demo (docs/demo-data.md): the artifacts are
synthetic tenant data and must never land in a real tenant's database.
"""

from __future__ import annotations

import csv
import gzip
import os
import sys
from pathlib import Path

from medstock_shared import engine
from medstock_shared.models import (
    ConsumptionDaily,
    Drug,
    Facility,
    FormularyItem,
    Hospital,
    LocationCondition,
    StockSnapshot,
    StorageLocation,
)
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .demo_layout import FACILITIES, HOSPITAL_ID, HOSPITAL_NAME, LOCATIONS, data_dir

BATCH = 5000


def _read_gz(path: Path) -> list[dict]:
    with gzip.open(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def _seed_hospital(s: Session) -> None:
    s.execute(
        insert(Hospital)
        .values(id=HOSPITAL_ID, name=HOSPITAL_NAME)
        .on_conflict_do_update(index_elements=["id"], set_={"name": HOSPITAL_NAME})
    )


def _seed_registry(s: Session) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    """Facilities and storage locations; returns (facility ids, location ids)."""
    for fac in FACILITIES:
        values = {
            "hospital_id": HOSPITAL_ID,
            "code": fac["code"],
            "name": fac["name"],
            "type": fac["type"],
            "lat": fac["lat"],
            "lon": fac["lon"],
            "operated": fac["operated"],
        }
        s.execute(
            insert(Facility)
            .values(**values)
            .on_conflict_do_update(index_elements=["hospital_id", "code"], set_=values)
        )
    fac_ids = {
        code: fid
        for fid, code in s.execute(
            select(Facility.id, Facility.code).where(Facility.hospital_id == HOSPITAL_ID)
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
            s.execute(
                insert(StorageLocation)
                .values(**values)
                .on_conflict_do_update(index_elements=["facility_id", "code"], set_=values)
            )
    loc_ids = {}
    for fac_code, fid in fac_ids.items():
        for lid, code in s.execute(
            select(StorageLocation.id, StorageLocation.code).where(
                StorageLocation.facility_id == fid
            )
        ):
            loc_ids[(fac_code, code)] = lid
    return fac_ids, loc_ids


def _seed_drugs(s: Session, drugs: list[dict]) -> None:
    """Drug is a global reference table; these are real NDCs with real RxCUIs,
    so upserting them is legitimate ingest — plus the demo storage classes."""
    for row in drugs:
        values = {
            "ndc": row["ndc"],
            "name": row["name"],
            "storage_class": row["storage_class"],
            "storage_min_c": float(row["storage_min_c"]),
            "storage_max_c": float(row["storage_max_c"]),
            "humidity_max_pct": float(row["humidity_max_pct"]),
            "raw": {"source": "demo", "rxcui": row["rxcui"], "cohort": row["cohort"]},
        }
        s.execute(
            insert(Drug)
            .values(**values)
            .on_conflict_do_update(index_elements=["ndc"], set_=values)
        )
        formulary = {"hospital_id": HOSPITAL_ID, "rxcui": row["rxcui"]}
        s.execute(
            insert(FormularyItem)
            .values(**formulary)
            .on_conflict_do_nothing(index_elements=["hospital_id", "rxcui"])
        )


def _seed_stock(s: Session, rows: list[dict], fac_ids: dict[str, int]) -> None:
    for row in rows:
        values = {
            "hospital_id": HOSPITAL_ID,
            "ndc": row["ndc"],
            "facility_id": fac_ids[row["facility"]],
            "location_id": row["location"],
            "quantity": int(row["qty"]),
        }
        s.execute(
            insert(StockSnapshot)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_stock_hospital_ndc_fac_loc",
                set_={"quantity": values["quantity"]},
            )
        )


def _seed_consumption(s: Session, rows: list[dict], fac_ids: dict[str, int]) -> None:
    """Bulk series: delete the demo tenant's slice, reload in batches."""
    s.execute(delete(ConsumptionDaily).where(ConsumptionDaily.hospital_id == HOSPITAL_ID))
    payload = [
        {
            "hospital_id": HOSPITAL_ID,
            "facility_id": fac_ids[row["facility"]],
            "ndc": row["ndc"],
            "rxcui": row["rxcui"],
            "date": row["date"],
            "qty_consumed": int(row["qty"]),
            "stockout": row["stockout"] == "1",
        }
        for row in rows
    ]
    for i in range(0, len(payload), BATCH):
        s.execute(insert(ConsumptionDaily), payload[i : i + BATCH])


def _seed_conditions(
    s: Session, rows: list[dict], loc_ids: dict[tuple[str, str], int]
) -> None:
    ids = list(loc_ids.values())
    s.execute(delete(LocationCondition).where(LocationCondition.location_id.in_(ids)))
    payload = [
        {
            "location_id": loc_ids[(row["facility"], row["location"])],
            "ts": row["ts"],
            "temperature_c": row["temperature_c"],
            "humidity_pct": row["humidity_pct"],
        }
        for row in rows
    ]
    for i in range(0, len(payload), BATCH):
        s.execute(insert(LocationCondition), payload[i : i + BATCH])


def run() -> dict[str, int]:
    if os.environ.get("ENVIRONMENT") != "demo":
        print("seed_demo: refusing — set ENVIRONMENT=demo (synthetic tenant data)", file=sys.stderr)
        raise SystemExit(1)
    src = data_dir()
    with (src / "drugs.csv").open() as fh:
        drugs = list(csv.DictReader(fh))
    stock = _read_gz(src / "stock.csv.gz")
    consumption = _read_gz(src / "consumption.csv.gz")
    conditions = _read_gz(src / "conditions.csv.gz")

    with Session(engine) as s:
        _seed_hospital(s)
        fac_ids, loc_ids = _seed_registry(s)
        _seed_drugs(s, drugs)
        _seed_stock(s, stock, fac_ids)
        _seed_consumption(s, consumption, fac_ids)
        _seed_conditions(s, conditions, loc_ids)
        s.commit()

    return {
        "drugs": len(drugs),
        "facilities": len(fac_ids),
        "locations": len(loc_ids),
        "stock": len(stock),
        "consumption": len(consumption),
        "conditions": len(conditions),
    }


if __name__ == "__main__":
    for name, count in run().items():
        print(f"seed_demo: {name}: {count} rows")
