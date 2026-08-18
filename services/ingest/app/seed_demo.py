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
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from medstock_shared import engine
from medstock_shared.demo_shelf import DASHBOARD_SHELF, demo_shortage_rows, shelf_stock_rows
from medstock_shared.forecasting import MODEL_VERSION
from medstock_shared.models import (
    ConsumptionDaily,
    Drug,
    ForecastPoint,
    FormularyItem,
    LocationCondition,
    ParLevel,
    ShortageEvent,
    StockBatch,
    StockDaily,
    StockSnapshot,
)
from medstock_shared.seed_wave4 import apply_partner_shortage, apply_suppliers
from sqlalchemy import delete, text
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


def _clear_shelf(s: Session, hospital_id: uuid.UUID) -> None:
    """Dashboard NDCs must not stack with leftover lots — unique is on lot."""
    ndcs = [d["ndc"] for d in DASHBOARD_SHELF]
    s.execute(delete(ParLevel).where(ParLevel.hospital_id == hospital_id, ParLevel.ndc.in_(ndcs)))
    s.execute(delete(StockBatch).where(StockBatch.hospital_id == hospital_id, StockBatch.ndc.in_(ndcs)))
    s.execute(
        delete(StockSnapshot).where(
            StockSnapshot.hospital_id == hospital_id, StockSnapshot.ndc.in_(ndcs)
        )
    )


def _upsert_batches(s: Session, rows: list[dict]) -> None:
    """Mirror snapshot rows into stock_batch + par_level (B4/B5).

    Dashboard rows carry mock lot / par / expiry_days. Other seed rows get a
    stable SEED- lot so a later run replaces rather than stacking. Actor GUC
    must already be set — the H1 trigger CHECKs it.
    """
    if not rows:
        return
    shelf = {d["ndc"]: d for d in DASHBOARD_SHELF}
    batches: list[dict] = []
    pars: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for row in rows:
        meta = shelf.get(row["ndc"], {})
        expiry = date.today() + timedelta(days=int(row.get("expiry_days") or meta.get("expiry_days", 365)))
        lot = row.get("lot") or f"SEED-{row['facility_id']}-{row['ndc']}-{row['location_id']}"
        batches.append(
            {
                "hospital_id": row["hospital_id"],
                "facility_id": row["facility_id"],
                "ndc": row["ndc"],
                "lot": lot,
                "expiry_date": expiry,
                "quantity": int(row["quantity"]),
                "location_id": row["location_id"],
            }
        )
        key = (row["facility_id"], row["ndc"])
        if key in seen:
            continue
        seen.add(key)
        qty = int(row["quantity"])
        reorder = int(row["par_reorder"]) if "par_reorder" in row else (
            int(meta["par_reorder"]) if "par_reorder" in meta else max(1, (qty // 4) or 1)
        )
        target = int(row["par_target"]) if "par_target" in row else (
            int(meta["par_target"]) if "par_target" in meta else max(reorder + 1, qty + reorder)
        )
        pars.append(
            {
                "hospital_id": row["hospital_id"],
                "facility_id": row["facility_id"],
                "ndc": row["ndc"],
                "reorder_point": reorder,
                "target_qty": target,
            }
        )
    for i in range(0, len(batches), BATCH):
        stmt = insert(StockBatch).values(batches[i : i + BATCH])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_stock_batch_natural",
            set_={
                "quantity": stmt.excluded.quantity,
                "location_id": stmt.excluded.location_id,
                "expiry_date": stmt.excluded.expiry_date,
            },
        )
        s.execute(stmt)
    for i in range(0, len(pars), BATCH):
        stmt = insert(ParLevel).values(pars[i : i + BATCH])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_par_level_natural",
            set_={
                "reorder_point": stmt.excluded.reorder_point,
                "target_qty": stmt.excluded.target_qty,
            },
        )
        s.execute(stmt)


def _seed_stock(
    s: Session, rows: list[dict], fac_ids: dict[str, int], hospital_id: uuid.UUID
) -> None:
    payload = []
    for row in rows:
        values = {
            "hospital_id": hospital_id,
            "ndc": row["ndc"],
            "facility_id": fac_ids[row["facility"]],
            "location_id": row["location"],
            "quantity": int(row["qty"]),
        }
        payload.append(values)
        s.execute(
            insert(StockSnapshot)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_stock_hospital_ndc_fac_loc",
                set_={"quantity": values["quantity"]},
            )
        )
    _upsert_batches(s, payload)


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
    at-risk list's in_shortage flag has something true to say (issue #7),
    plus the three mock shortage-alert SKUs so B3 uncovered is a real claim.
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
    for row in demo_shortage_rows():
        s.execute(
            insert(ShortageEvent)
            .values(**row)
            .on_conflict_do_update(
                index_elements=["source_id"],
                set_={"ndc": row["ndc"], "status": row["status"], "raw": row["raw"]},
            )
        )
        planted += 1
    return planted


def _overlay_dashboard_shelf(
    s: Session,
    drugs: list[dict],
    consumption: list[dict],
    stock_history: list[dict],
    fac_ids: dict[str, int],
    hospital_id: uuid.UUID,
) -> int:
    """Plant the inventory-page NDCs so Warehouse charts aren't empty for them.

    gen_demo's 100-drug panel doesn't include the dashboard shelf (different
    NDCs, chosen for COMP-1). Without this overlay the warehouse picker either
    omits those SKUs or shows them with no consumption/conditions join.
    Consumption shape is cloned from a same-class donor already in the artifact.
    """
    donor_by_class: dict[str, dict] = {}
    for drug in drugs:
        if drug["stockout_prone"] == "True":
            continue
        donor_by_class.setdefault(drug["storage_class"], drug)

    cons_by_ndc: dict[str, list[dict]] = defaultdict(list)
    for row in consumption:
        cons_by_ndc[row["ndc"]].append(row)
    hist_by_ndc: dict[str, list[dict]] = defaultdict(list)
    for row in stock_history:
        hist_by_ndc[row["ndc"]].append(row)

    for item in DASHBOARD_SHELF:
        cls = item["storage_class"]
        donor = donor_by_class.get(cls)
        if donor is None:
            continue
        drug_values = {
            "ndc": item["ndc"],
            "name": item["name"],
            "storage_class": cls,
            "storage_min_c": item["storage_min_c"],
            "storage_max_c": item["storage_max_c"],
            "humidity_max_pct": item["humidity_max_pct"],
            "raw": {"source": "demo-shelf", "rxcui": item["rxcui"]},
        }
        s.execute(
            insert(Drug)
            .values(**drug_values)
            .on_conflict_do_update(index_elements=["ndc"], set_=drug_values)
        )
        s.execute(
            insert(FormularyItem)
            .values(hospital_id=hospital_id, rxcui=item["rxcui"])
            .on_conflict_do_nothing(index_elements=["hospital_id", "rxcui"])
        )

    _clear_shelf(s, hospital_id)
    extra_stock = shelf_stock_rows(hospital_id, fac_ids)
    id_by_fac = {fid: code for code, fid in fac_ids.items()}
    item_by_ndc = {d["ndc"]: d for d in DASHBOARD_SHELF}

    extra_cons: list[dict] = []
    extra_hist: list[dict] = []
    for row in extra_stock:
        item = item_by_ndc[row["ndc"]]
        donor = donor_by_class.get(item["storage_class"])
        if donor is None:
            continue
        rxcui = item["rxcui"]
        fac_code = id_by_fac[row["facility_id"]]
        qty = int(row["quantity"])
        for cons in cons_by_ndc[donor["ndc"]]:
            if cons["facility"] != fac_code:
                continue
            extra_cons.append(
                {
                    "hospital_id": hospital_id,
                    "facility_id": row["facility_id"],
                    "ndc": row["ndc"],
                    "rxcui": rxcui,
                    "date": cons["date"],
                    "qty_consumed": int(cons["qty"]),
                    "stockout": cons["stockout"] == "1",
                }
            )
        donor_hist = [r for r in hist_by_ndc[donor["ndc"]] if r["facility"] == fac_code]
        donor_last = int(donor_hist[-1]["qty"]) if donor_hist else 0
        factor = (qty / donor_last) if donor_last else 1.0
        for i, hist in enumerate(donor_hist):
            extra_hist.append(
                {
                    "hospital_id": hospital_id,
                    "facility_id": row["facility_id"],
                    "ndc": row["ndc"],
                    "date": hist["date"],
                    "qty_on_hand": qty if i == len(donor_hist) - 1 else max(0, round(int(hist["qty"]) * factor)),
                }
            )

    if extra_stock:
        snaps = [
            {
                "hospital_id": row["hospital_id"],
                "ndc": row["ndc"],
                "facility_id": row["facility_id"],
                "location_id": row["location_id"],
                "quantity": row["quantity"],
            }
            for row in extra_stock
        ]
        for i in range(0, len(snaps), BATCH):
            stmt = insert(StockSnapshot).values(snaps[i : i + BATCH])
            stmt = stmt.on_conflict_do_update(
                constraint="uq_stock_hospital_ndc_fac_loc",
                set_={"quantity": stmt.excluded.quantity},
            )
            s.execute(stmt)
        _upsert_batches(s, extra_stock)
    if extra_cons:
        s.execute(
            delete(ConsumptionDaily).where(
                ConsumptionDaily.hospital_id == hospital_id,
                ConsumptionDaily.ndc.in_([d["ndc"] for d in DASHBOARD_SHELF]),
            )
        )
        for i in range(0, len(extra_cons), BATCH):
            s.execute(insert(ConsumptionDaily), extra_cons[i : i + BATCH])
    if extra_hist:
        s.execute(
            delete(StockDaily).where(
                StockDaily.hospital_id == hospital_id,
                StockDaily.ndc.in_([d["ndc"] for d in DASHBOARD_SHELF]),
            )
        )
        for i in range(0, len(extra_hist), BATCH):
            s.execute(insert(StockDaily), extra_hist[i : i + BATCH])
    return len(extra_stock)


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
        s.execute(
            text(
                "SELECT set_config('app.hospital_id', :h, true), "
                "set_config('app.actor_id', '', true), "
                "set_config('app.actor_system', 'seed_demo', true)"
            ),
            {"h": str(hospital_id)},
        )
        fac_ids, loc_ids = upsert_registry(s, hospital_id)
        _seed_drugs(s, drugs, hospital_id)
        _seed_stock(s, stock, fac_ids, hospital_id)
        _seed_consumption(s, consumption, fac_ids, hospital_id)
        _seed_conditions(s, conditions, loc_ids)
        _seed_forecast(s, forecast, fac_ids, hospital_id)
        _seed_stock_history(s, stock_history, fac_ids, hospital_id)
        shelf = _overlay_dashboard_shelf(s, drugs, consumption, stock_history, fac_ids, hospital_id)
        partners = apply_partner_shortage(s, hospital_id, fac_ids)
        shortages = _seed_shortages(s, drugs)
        suppliers = apply_suppliers(s, hospital_id)
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
        "dashboard_shelf": shelf,
        "partner_shortage_rows": partners,
        "shortages": shortages,
        "suppliers": suppliers,
    }


if __name__ == "__main__":
    for name, count in run().items():
        print(f"seed_demo: {name}: {count} rows")
