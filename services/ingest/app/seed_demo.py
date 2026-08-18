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
import uuid
from pathlib import Path

from medstock_shared import engine
from medstock_shared.forecasting import MODEL_VERSION
from medstock_shared.models import (
    ConsumptionDaily,
    Drug,
    ForecastPoint,
    FormularyItem,
    LocationCondition,
    ShortageEvent,
    StockDaily,
    StockSnapshot,
)
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .demo_layout import (
    END_DATE,
    data_dir,
    resolve_or_create_hospital,
    upsert_registry,
)

BATCH = 5000

# The committed run's identity is derived, not random, so reseeding is
# idempotent and the run a screenshot cites is the same run every time.
DEMO_RUN_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "medstock-demo-forecast"))


def _read_gz(path: Path) -> list[dict]:
    with gzip.open(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def _seed_drugs(s: Session, drugs: list[dict], hospital_id: uuid.UUID) -> None:
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
        formulary = {"hospital_id": hospital_id, "rxcui": row["rxcui"]}
        s.execute(
            insert(FormularyItem)
            .values(**formulary)
            .on_conflict_do_nothing(index_elements=["hospital_id", "rxcui"])
        )


def _seed_stock(
    s: Session, rows: list[dict], fac_ids: dict[str, int], hospital_id: uuid.UUID
) -> None:
    for row in rows:
        values = {
            "hospital_id": hospital_id,
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


def _seed_consumption(
    s: Session, rows: list[dict], fac_ids: dict[str, int], hospital_id: uuid.UUID
) -> None:
    """Bulk series: delete the demo tenant's slice, reload in batches."""
    s.execute(delete(ConsumptionDaily).where(ConsumptionDaily.hospital_id == hospital_id))
    payload = [
        {
            "hospital_id": hospital_id,
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


def _seed_stock_history(
    s: Session, rows: list[dict], fac_ids: dict[str, int], hospital_id: uuid.UUID
) -> None:
    """Bulk series: delete the demo tenant's slice, reload in batches — same
    treatment as consumption. Ends exactly at stock_snapshot's quantities so
    the forecasts page's history meets its projection without a jump."""
    s.execute(delete(StockDaily).where(StockDaily.hospital_id == hospital_id))
    payload = [
        {
            "hospital_id": hospital_id,
            "facility_id": fac_ids[row["facility"]],
            "ndc": row["ndc"],
            "date": row["date"],
            "qty_on_hand": int(row["qty"]),
        }
        for row in rows
    ]
    for i in range(0, len(payload), BATCH):
        s.execute(insert(StockDaily), payload[i : i + BATCH])


def _seed_forecast(
    s: Session, rows: list[dict], fac_ids: dict[str, int], hospital_id: uuid.UUID
) -> None:
    """The canonical demo run (issue #7): delete-and-reload like the other
    bulk series. data_through = END_DATE — pinned, so the artifact stays
    stable as calendar time passes; POST /forecast/runs recomputes live."""
    s.execute(delete(ForecastPoint).where(ForecastPoint.hospital_id == hospital_id))
    payload = [
        {
            "hospital_id": hospital_id,
            "facility_id": fac_ids[row["facility"]],
            "ndc": row["ndc"],
            "run_id": DEMO_RUN_ID,
            "data_through": END_DATE,
            "target_date": row["date"],
            "p10": row["p10"],
            "p50": row["p50"],
            "p90": row["p90"],
            "model_version": MODEL_VERSION,
        }
        for row in rows
    ]
    for i in range(0, len(payload), BATCH):
        s.execute(insert(ForecastPoint), payload[i : i + BATCH])


def _seed_shortages(s: Session, drugs: list[dict]) -> int:
    """Plant an active shortage_event on the stockout-prone drugs so the
    at-risk list's in_shortage flag has something true to say (issue #7).
    shortage_event is a global reference table; the ENVIRONMENT=demo gate in
    run() is what keeps this out of real databases."""
    planted = 0
    for drug in drugs:
        if drug["stockout_prone"] != "True":
            continue
        s.execute(
            insert(ShortageEvent)
            .values(
                source_id=f"demo-shortage-{drug['ndc']}",
                ndc=drug["ndc"],
                status="Current",
                raw={"note": "planted by seed_demo", "name": drug["name"]},
            )
            .on_conflict_do_update(
                index_elements=["source_id"],
                set_={"ndc": drug["ndc"], "status": "Current"},
            )
        )
        planted += 1
    return planted


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
    forecast = _read_gz(src / "forecast.csv.gz")
    stock_history = _read_gz(src / "stock_history.csv.gz")

    with Session(engine) as s:
        hospital_id = resolve_or_create_hospital(s)
        fac_ids, loc_ids = upsert_registry(s, hospital_id)
        _seed_drugs(s, drugs, hospital_id)
        _seed_stock(s, stock, fac_ids, hospital_id)
        _seed_consumption(s, consumption, fac_ids, hospital_id)
        _seed_conditions(s, conditions, loc_ids)
        _seed_forecast(s, forecast, fac_ids, hospital_id)
        _seed_stock_history(s, stock_history, fac_ids, hospital_id)
        shortages = _seed_shortages(s, drugs)
        s.commit()

    return {
        "drugs": len(drugs),
        "facilities": len(fac_ids),
        "locations": len(loc_ids),
        "stock": len(stock),
        "consumption": len(consumption),
        "conditions": len(conditions),
        "forecast": len(forecast),
        "stock_history": len(stock_history),
        "shortages": shortages,
    }


if __name__ == "__main__":
    for name, count in run().items():
        print(f"seed_demo: {name}: {count} rows")
