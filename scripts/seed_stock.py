"""UC-2 demo data: upsert stock_snapshot (+ a formulary subset) for the next-dev hospital.

Resolves NDCs via the shared RxNorm client (live ndcs.json, curated fallback when
empty — aspirin 100 MG Oral Tablet RxCUI 246461 has no current US packs). Re-running
does not duplicate: unique (hospital_id, ndc, facility_id, location_id) /
(hospital_id, rxcui). Locations come from the same registry seed_demo uses
(medstock_shared.demo_tenant) — not a parallel main-pharmacy/icu/ward-3 scheme.

  PYTHONPATH=shared python scripts/seed_stock.py
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

# scripts/ → repo root on sys.path is not enough; shared lives in shared/.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "shared"))

from medstock_shared.db import SessionLocal
from medstock_shared.demo_shelf import DASHBOARD_SHELF, shelf_stock_rows
from medstock_shared.demo_tenant import (
    FACILITIES,
    HOSPITAL_NAME,
    LOCATIONS,
    OPERATED_CODES,
    location_for,
    upsert_registry,
)
from medstock_shared.models import Drug, FormularyItem, Hospital, ParLevel, StockBatch, StockSnapshot
from medstock_shared.rxnorm import (
    CURATED_NDCS_WHEN_EMPTY,
    RxNormError,
    ndcs_for_rxcui,
)
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

# Resolve by name (St Mary's General) and refuse to guess. A fallback UUID
# used to plant an invisible shelf: ingest-certification certifies
# shelf_ndcs(), so rows in a hospital nobody's token names make every
# dashboard badge read "unknown".
NDC_CAP = 6
RNG_SEED = 42
SHELF_FACILITY = "central"
SHELF_LOCATION = "main-room"

# Real SCD/SBD RxCUIs. `in_formulary` is a subset so UC-1 search boost still shows.
DRUGS: tuple[dict, ...] = (
    {"rxcui": "246461", "name": "aspirin 100 MG Oral Tablet", "in_formulary": True},
    {"rxcui": "861007", "name": "metformin hydrochloride 500 MG Oral Tablet", "in_formulary": True},
    {"rxcui": "308182", "name": "amoxicillin 250 MG Oral Capsule", "in_formulary": False},
    {"rxcui": "617310", "name": "atorvastatin 20 MG Oral Tablet", "in_formulary": True},
    {"rxcui": "312086", "name": "ondansetron 8 MG Oral Tablet", "in_formulary": False},
    {"rxcui": "314076", "name": "lisinopril 10 MG Oral Tablet", "in_formulary": False},
    {"rxcui": "311700", "name": "midazolam 1 MG/ML Injectable Solution", "in_formulary": True},
    {"rxcui": "200801", "name": "furosemide 20 MG Oral Tablet [Lasix]", "in_formulary": False},
)

# The SKUs the dashboard shelf shows (web/lib/mock-data.ts). The NDC list is
# pinned by services/compliance/tests/test_demo_shelf.py against demo_shelf.py
# (imported above) so seed_demo can plant the same rows with consumption.

# Optional `quantity` (all sites) and `sites` freeze demo stock; otherwise random
# operated shelves from demo_tenant. 197603 is on live GET /analogues/212033
# (diflunisal — not aspirin). High band is quantity > 100 so this analogue ranks
# first in Full. 198479 is aspirin+caffeine: Ingredient can show it; Full hides
# same-ingredient products.
ANALOGUE_DRUGS: tuple[dict, ...] = (
    {
        "rxcui": "197603",
        "name": "diflunisal 500 MG Oral Tablet",
        "in_formulary": False,
        "quantity": 180,
        "sites": ((SHELF_FACILITY, SHELF_LOCATION),),
    },
    {
        "rxcui": "198479",
        "name": "aspirin 400 MG / caffeine 32 MG Oral Tablet",
        "in_formulary": False,
        "quantity": 40,
        "sites": ((SHELF_FACILITY, SHELF_LOCATION),),
    },
)


def _operated_shelves() -> list[tuple[str, str]]:
    return [
        (fac["code"], loc[0])
        for fac in FACILITIES
        if fac["operated"]
        for loc in LOCATIONS[fac["code"]]
    ]


def resolve_ndcs(rxcui: str, rng: random.Random) -> list[str]:
    try:
        ndcs = ndcs_for_rxcui(rxcui)
    except RxNormError:
        ndcs = list(CURATED_NDCS_WHEN_EMPTY.get(rxcui, []))
    ndcs = list(dict.fromkeys(ndcs))
    if len(ndcs) > NDC_CAP:
        ndcs = rng.sample(ndcs, NDC_CAP)
    return ndcs


def build_stock_rows(
    hospital_id: uuid.UUID,
    drugs: tuple[dict, ...],
    rng: random.Random,
    fac_ids: dict[str, int],
    *,
    demo_edges: bool = True,
) -> list[dict]:
    rows: list[dict] = []
    aspirin_positive = False
    shelves = _operated_shelves()
    for drug in drugs:
        ndcs = resolve_ndcs(drug["rxcui"], rng)
        if not ndcs:
            print(f"skip {drug['rxcui']} {drug['name']}: no NDCs", file=sys.stderr)
            continue
        print(f"{drug['rxcui']} {drug['name']}: {len(ndcs)} NDC(s)")
        for ndc in ndcs:
            if "sites" in drug:
                chosen = list(drug["sites"])
            else:
                loc_count = 1 if rng.random() < 0.65 else 2
                chosen = rng.sample(shelves, loc_count)
            for facility_code, location_id in chosen:
                quantity = int(drug["quantity"]) if "quantity" in drug else rng.randint(0, 400)
                if drug["rxcui"] == "246461" and quantity > 0:
                    aspirin_positive = True
                rows.append(
                    {
                        "hospital_id": hospital_id,
                        "ndc": ndc,
                        "facility_id": fac_ids[facility_code],
                        "location_id": location_id,
                        "quantity": quantity,
                    }
                )
    if demo_edges:
        if not any(r["quantity"] == 0 for r in rows) and rows:
            rows[0]["quantity"] = 0
        if not aspirin_positive:
            for row in rows:
                if row["ndc"] in CURATED_NDCS_WHEN_EMPTY.get("246461", []):
                    row["quantity"] = max(row["quantity"], rng.randint(12, 180))
                    break
    return rows


def build_shelf_rows(hospital_id: uuid.UUID, fac_ids: dict[str, int]) -> list[dict]:
    """Mock inventory rows at every operated site, same profile as inventoryFor()."""
    rows = shelf_stock_rows(hospital_id, fac_ids)
    for row in rows:
        print(f"{row['ndc']} {row['lot']} → facility={row['facility_id']}/{row['location_id']} qty={row['quantity']}")
    return rows


def upsert(session: Session, hospital_id: uuid.UUID, rows: list[dict], formulary: list[str]) -> None:
    if rows:
        snaps = [
            {
                "hospital_id": row["hospital_id"],
                "ndc": row["ndc"],
                "facility_id": row["facility_id"],
                "location_id": row["location_id"],
                "quantity": row["quantity"],
            }
            for row in rows
        ]
        stmt = insert(StockSnapshot).values(snaps)
        # uq_stock_hospital_ndc_fac_loc, not uq_stock_hospital_ndc_loc: the
        # warehouse migration (20260817_warehouse) added facility_id to the
        # natural key, because location codes repeat across facilities — every
        # clinic has a "fridge-1". The old name no longer exists, so this script
        # raised UndefinedObject against any database at current head, which is
        # every environment the runbook tells you to seed.
        stmt = stmt.on_conflict_do_update(
            constraint="uq_stock_hospital_ndc_fac_loc",
            set_={"quantity": stmt.excluded.quantity, "updated_at": func.now()},
        )
        session.execute(stmt)
        batches = []
        pars = []
        seen_par: set[tuple[int, str]] = set()
        shelf_by_ndc = {d["ndc"]: d for d in DASHBOARD_SHELF}
        for row in rows:
            shelf = shelf_by_ndc.get(row["ndc"], {})
            expiry = date.today() + timedelta(
                days=int(row.get("expiry_days") or shelf.get("expiry_days", 365))
            )
            lot = row.get("lot") or f"SEED-{row['facility_id']}-{row['ndc']}-{row['location_id']}"
            batches.append(
                {
                    "hospital_id": row["hospital_id"],
                    "facility_id": row["facility_id"],
                    "ndc": row["ndc"],
                    "lot": lot,
                    "expiry_date": expiry,
                    "quantity": row["quantity"],
                    "location_id": row["location_id"],
                }
            )
            key = (row["facility_id"], row["ndc"])
            if key in seen_par:
                continue
            seen_par.add(key)
            qty = int(row["quantity"])
            reorder = int(row["par_reorder"]) if "par_reorder" in row else (
                int(shelf["par_reorder"]) if "par_reorder" in shelf else max(1, qty // 4)
            )
            target = int(row["par_target"]) if "par_target" in row else (
                int(shelf["par_target"]) if "par_target" in shelf else max(reorder + 1, qty + reorder)
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
        bstmt = insert(StockBatch).values(batches)
        bstmt = bstmt.on_conflict_do_update(
            constraint="uq_stock_batch_natural",
            set_={
                "quantity": bstmt.excluded.quantity,
                "location_id": bstmt.excluded.location_id,
                "expiry_date": bstmt.excluded.expiry_date,
            },
        )
        session.execute(bstmt)
        pstmt = insert(ParLevel).values(pars)
        pstmt = pstmt.on_conflict_do_update(
            constraint="uq_par_level_natural",
            set_={
                "reorder_point": pstmt.excluded.reorder_point,
                "target_qty": pstmt.excluded.target_qty,
                "updated_at": func.now(),
            },
        )
        session.execute(pstmt)
    if formulary:
        form_rows = [{"hospital_id": hospital_id, "rxcui": rxcui} for rxcui in formulary]
        fstmt = insert(FormularyItem).values(form_rows)
        fstmt = fstmt.on_conflict_do_nothing(constraint="uq_formulary_hospital_rxcui")
        session.execute(fstmt)


def resolve_hospital_id(session: Session, explicit: str | None, name: str) -> uuid.UUID:
    """The tenant to seed into — named, not assumed.

    An explicit `--hospital-id` always wins; tests and one-off environments need
    to name a tenant with no `hospital` row at all. Otherwise resolve by name
    and, if it is not there, **stop**. Falling back to a constant would fill the
    shelf with rows nobody can see and report success.
    """
    if explicit:
        return uuid.UUID(explicit.strip())

    row = session.execute(select(Hospital).where(Hospital.name == name)).scalars().first()
    if row is None:
        raise SystemExit(
            f"no hospital named {name!r} — nothing to seed into. "
            "Run the auth seed first (python -m app.seed in services/auth, or "
            "deploy/k8s/seed-job.yaml), or pass --hospital-id explicitly."
        )
    return row.id


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed UC-2 stock_snapshot demo rows")
    parser.add_argument(
        "--hospital-id",
        default=None,
        help="tenant to seed into; defaults to whichever hospital --hospital-name resolves to",
    )
    parser.add_argument(
        "--hospital-name",
        default=HOSPITAL_NAME,
        help="resolve the tenant by name instead of by uuid",
    )
    args = parser.parse_args()

    session = SessionLocal()
    try:
        hospital_id = resolve_hospital_id(session, args.hospital_id, args.hospital_name)
        session.execute(
            text(
                "SELECT set_config('app.hospital_id', :h, true), "
                "set_config('app.actor_id', '', true), "
                "set_config('app.actor_system', 'seed_stock', true)"
            ),
            {"h": str(hospital_id)},
        )
        fac_ids, _loc_ids = upsert_registry(session, hospital_id)
        rng = random.Random(RNG_SEED)
        shelf_ndcs = {d["ndc"] for d in DASHBOARD_SHELF}
        rows = build_stock_rows(hospital_id, DRUGS, rng, fac_ids)
        rows.extend(build_stock_rows(hospital_id, ANALOGUE_DRUGS, rng, fac_ids, demo_edges=False))
        rows = [r for r in rows if r["ndc"] not in shelf_ndcs]
        session.execute(
            delete(ParLevel).where(ParLevel.hospital_id == hospital_id, ParLevel.ndc.in_(shelf_ndcs))
        )
        session.execute(
            delete(StockBatch).where(StockBatch.hospital_id == hospital_id, StockBatch.ndc.in_(shelf_ndcs))
        )
        session.execute(
            delete(StockSnapshot).where(
                StockSnapshot.hospital_id == hospital_id, StockSnapshot.ndc.in_(shelf_ndcs)
            )
        )
        rows.extend(build_shelf_rows(hospital_id, fac_ids))
        formulary = [d["rxcui"] for d in (*DRUGS, *ANALOGUE_DRUGS) if d["in_formulary"]]
        for item in DASHBOARD_SHELF:
            values = {
                "ndc": item["ndc"],
                "name": item["name"],
                "storage_class": item["storage_class"],
                "storage_min_c": item["storage_min_c"],
                "storage_max_c": item["storage_max_c"],
                "humidity_max_pct": item["humidity_max_pct"],
                "raw": {"source": "demo-shelf"},
            }
            session.execute(
                insert(Drug)
                .values(**values)
                .on_conflict_do_update(index_elements=["ndc"], set_=values)
            )
        upsert(session, hospital_id, rows, formulary)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    print(
        f"upserted {len(rows)} stock line(s) "
        f"({len(DASHBOARD_SHELF)} of them the dashboard shelf), "
        f"{len(formulary)} formulary rxcui(s) "
        f"for hospital_id={hospital_id} facilities={','.join(OPERATED_CODES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
