"""UC-2 demo data: upsert stock_snapshot (+ a formulary subset) for the next-dev hospital.

Resolves NDCs via the shared RxNorm client (live ndcs.json, curated fallback when
empty — aspirin 100 MG Oral Tablet RxCUI 246461 has no current US packs). Re-running
does not duplicate: unique (hospital_id, ndc, location_id) / (hospital_id, rxcui).

  PYTHONPATH=shared python scripts/seed_stock.py
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

# scripts/ → repo root on sys.path is not enough; shared lives in shared/.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "shared"))

from medstock_shared.db import SessionLocal
from medstock_shared.models import FormularyItem, Hospital, StockSnapshot
from medstock_shared.rxnorm import (
    CURATED_NDCS_WHEN_EMPTY,
    RxNormError,
    ndcs_for_rxcui,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

# `stock_snapshot.hospital_id` and `formulary.hospital_id` are Text with no
# foreign key, so seeding into a hospital that does not exist succeeds and
# writes rows nobody can see — a user only ever sees the hospital named in their
# token. This script used to default to the literal
# 00000000-0000-0000-0000-000000000001, which nothing creates: the only place a
# `hospital` row is made is services/auth/app/seed.py, and it lets Postgres
# generate the uuid.
#
# That default is not a cosmetic problem here. `ingest-certification` certifies
# `shelf_ndcs()`, which reads stock_snapshot — so an invisible shelf means the
# daily job certifies drugs nobody can see and every badge on the dashboard sits
# at "unknown". Same fix as scripts/seed_patients.py: resolve by name, refuse to
# guess.
DEFAULT_HOSPITAL_NAME = "St Mary's General"  # keep in step with auth's seed
LOCATIONS = ("main-pharmacy", "icu", "ward-3")
NDC_CAP = 6
RNG_SEED = 42

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

# The SKUs the dashboard shelf shows (web/lib/mock-data.ts), pinned to this
# list by services/compliance/tests/test_demo_shelf.py.
#
# Seeded by NDC rather than resolved through RxNorm like DRUGS above, because
# these were chosen *as* NDCs: each is a real, currently-listed product picked so
# COMP-1 has something genuine to say about it. Going back through an RxCUI would
# hand back a different pack and lose exactly that.
#
# They belong in stock_snapshot because that is what `shelf_ndcs()` reads, and
# the ingest-certification CronJob certifies the shelf. Without these rows the
# daily job certifies drugs nobody can see and every badge on the dashboard sits
# at "unknown".
DASHBOARD_SHELF: tuple[dict, ...] = (
    {"ndc": "62135009120", "name": "Amoxicillin/Clavulanate 875mg", "quantity": 900},
    {"ndc": "16714097720", "name": "Propofol 1% Emulsion", "quantity": 250},
    {"ndc": "82804006601", "name": "Ceftriaxone 1g", "quantity": 9},
    {"ndc": "00487990130", "name": "Salbutamol 100mcg Inhaler", "quantity": 140},
    {"ndc": "00338011220", "name": "Norepinephrine 4mg/4mL", "quantity": 60},
    {"ndc": "00069406101", "name": "Azithromycin 250mg", "quantity": 420},
    {"ndc": "00024586900", "name": "Insulin Glargine 100U/mL", "quantity": 75},
    {"ndc": "63323041125", "name": "Midazolam 5mg/mL", "quantity": 180},
    {"ndc": "00143938610", "name": "Paracetamol 1g IV", "quantity": 300},
    {"ndc": "00338043304", "name": "Heparin Sodium 5000IU/mL", "quantity": 95},
    # Obsolete in RxNorm, so the certification traffic light has a red to show.
    # The two rows above were picked for open Class I recalls and both closed --
    # the feed working, not failing, but it left the shelf with no red on it.
    {"ndc": "76168080030", "name": "Carmellose Sodium 0.5% Eye Drops", "quantity": 62},
)

# Optional `quantity` (all locations) and `locations` freeze demo stock; otherwise random.
# 197603 is on live GET /analogues/212033?mode=full (diflunisal — not aspirin).
# High band is quantity > 100 so this analogue ranks first in Full.
# 198479 is aspirin+caffeine: Ingredient can show it; Full hides same-ingredient products.
ANALOGUE_DRUGS: tuple[dict, ...] = (
    {
        "rxcui": "197603",
        "name": "diflunisal 500 MG Oral Tablet",
        "in_formulary": False,
        "quantity": 180,
        "locations": ("main-pharmacy",),
    },
    {
        "rxcui": "198479",
        "name": "aspirin 400 MG / caffeine 32 MG Oral Tablet",
        "in_formulary": False,
        "quantity": 40,
        "locations": ("main-pharmacy",),
    },
)


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
    hospital_id: str,
    drugs: tuple[dict, ...],
    rng: random.Random,
    *,
    demo_edges: bool = True,
) -> list[dict]:
    rows: list[dict] = []
    aspirin_positive = False
    for drug in drugs:
        ndcs = resolve_ndcs(drug["rxcui"], rng)
        if not ndcs:
            print(f"skip {drug['rxcui']} {drug['name']}: no NDCs", file=sys.stderr)
            continue
        print(f"{drug['rxcui']} {drug['name']}: {len(ndcs)} NDC(s)")
        for ndc in ndcs:
            if "locations" in drug:
                chosen = list(drug["locations"])
            else:
                loc_count = 1 if rng.random() < 0.65 else 2
                chosen = rng.sample(LOCATIONS, loc_count)
            for location_id in chosen:
                quantity = int(drug["quantity"]) if "quantity" in drug else rng.randint(0, 400)
                if drug["rxcui"] == "246461" and quantity > 0:
                    aspirin_positive = True
                rows.append(
                    {
                        "hospital_id": hospital_id,
                        "ndc": ndc,
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


def build_shelf_rows(hospital_id: str) -> list[dict]:
    """The dashboard shelf, one line each at the main pharmacy.

    No RNG and no RxNorm round-trip: these NDCs are fixed so that what the
    CronJob certifies is exactly what the screen shows.
    """
    rows = [
        {
            "hospital_id": hospital_id,
            "ndc": drug["ndc"],
            "location_id": "main-pharmacy",
            "quantity": int(drug["quantity"]),
        }
        for drug in DASHBOARD_SHELF
    ]
    for drug in DASHBOARD_SHELF:
        print(f"{drug['ndc']} {drug['name']}")
    return rows


def upsert(session: Session, hospital_id: str, rows: list[dict], formulary: list[str]) -> None:
    if rows:
        stmt = insert(StockSnapshot).values(rows)
        # uq_stock_hospital_ndc_fac_loc, not uq_stock_hospital_ndc_loc: the
        # warehouse migration (20260817_warehouse) added facility_id to the
        # natural key, because location codes repeat across facilities — every
        # clinic has a "fridge-1". The old name no longer exists, so this script
        # raised UndefinedObject against any database at current head, which is
        # every environment the runbook tells you to seed.
        #
        # These rows carry no facility_id. The constraint is NULLS NOT DISTINCT,
        # so they still de-duplicate on (hospital_id, ndc, location_id).
        stmt = stmt.on_conflict_do_update(
            constraint="uq_stock_hospital_ndc_fac_loc",
            set_={"quantity": stmt.excluded.quantity, "updated_at": func.now()},
        )
        session.execute(stmt)
    if formulary:
        form_rows = [{"hospital_id": hospital_id, "rxcui": rxcui} for rxcui in formulary]
        fstmt = insert(FormularyItem).values(form_rows)
        fstmt = fstmt.on_conflict_do_nothing(constraint="uq_formulary_hospital_rxcui")
        session.execute(fstmt)


def resolve_hospital_id(session: Session, explicit: str | None, name: str) -> str:
    """The tenant to seed into — named, not assumed.

    An explicit `--hospital-id` always wins; tests and one-off environments need
    to name a tenant with no `hospital` row at all. Otherwise resolve by name
    and, if it is not there, **stop**. Falling back to a constant would fill the
    shelf with rows nobody can see and report success.
    """
    if explicit:
        return explicit.strip()

    row = session.execute(select(Hospital).where(Hospital.name == name)).scalars().first()
    if row is None:
        raise SystemExit(
            f"no hospital named {name!r} — nothing to seed into. "
            "Run the auth seed first (python -m app.seed in services/auth, or "
            "deploy/k8s/seed-job.yaml), or pass --hospital-id explicitly."
        )
    return str(row.id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed UC-2 stock_snapshot demo rows")
    parser.add_argument(
        "--hospital-id",
        default=None,
        help="tenant to seed into; defaults to whichever hospital --hospital-name resolves to",
    )
    parser.add_argument(
        "--hospital-name",
        default=DEFAULT_HOSPITAL_NAME,
        help="resolve the tenant by name instead of by uuid",
    )
    args = parser.parse_args()

    session = SessionLocal()
    try:
        hospital_id = resolve_hospital_id(session, args.hospital_id, args.hospital_name)
        rng = random.Random(RNG_SEED)
        rows = build_stock_rows(hospital_id, DRUGS, rng)
        rows.extend(build_stock_rows(hospital_id, ANALOGUE_DRUGS, rng, demo_edges=False))
        rows.extend(build_shelf_rows(hospital_id))
        formulary = [d["rxcui"] for d in (*DRUGS, *ANALOGUE_DRUGS) if d["in_formulary"]]
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
        f"for hospital_id={hospital_id} locations={','.join(LOCATIONS)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
