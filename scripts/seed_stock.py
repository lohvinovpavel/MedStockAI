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

from sqlalchemy import func  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from medstock_shared.db import SessionLocal  # noqa: E402
from medstock_shared.models import FormularyItem, StockSnapshot  # noqa: E402
from medstock_shared.rxnorm import (  # noqa: E402
    CURATED_NDCS_WHEN_EMPTY,
    RxNormError,
    ndcs_for_rxcui,
)

# Same claim as web/.env.local / /tmp/medstock-dev/token.txt
DEFAULT_HOSPITAL_ID = "00000000-0000-0000-0000-000000000001"
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


def upsert(session: Session, hospital_id: str, rows: list[dict], formulary: list[str]) -> None:
    if rows:
        stmt = insert(StockSnapshot).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_stock_hospital_ndc_loc",
            set_={"quantity": stmt.excluded.quantity, "updated_at": func.now()},
        )
        session.execute(stmt)
    if formulary:
        form_rows = [{"hospital_id": hospital_id, "rxcui": rxcui} for rxcui in formulary]
        fstmt = insert(FormularyItem).values(form_rows)
        fstmt = fstmt.on_conflict_do_nothing(constraint="uq_formulary_hospital_rxcui")
        session.execute(fstmt)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed UC-2 stock_snapshot demo rows")
    parser.add_argument("--hospital-id", default=DEFAULT_HOSPITAL_ID)
    args = parser.parse_args()
    rng = random.Random(RNG_SEED)
    rows = build_stock_rows(args.hospital_id, DRUGS, rng)
    rows.extend(
        build_stock_rows(args.hospital_id, ANALOGUE_DRUGS, rng, demo_edges=False)
    )
    formulary = [
        d["rxcui"] for d in (*DRUGS, *ANALOGUE_DRUGS) if d["in_formulary"]
    ]
    session = SessionLocal()
    try:
        upsert(session, args.hospital_id, rows, formulary)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    print(
        f"upserted {len(rows)} stock line(s), {len(formulary)} formulary rxcui(s) "
        f"for hospital_id={args.hospital_id} locations={','.join(LOCATIONS)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
