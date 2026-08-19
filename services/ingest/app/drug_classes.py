"""CronJob entrypoint, on demand: RxClass -> drug.drug_class backfill.

Walks drugs whose raw JSONB carries an rxcui (all demo drugs do) and fills the
primary therapeutic class (ATC preferred, VA/MESHPA fallback) via the shared
RxNorm client — the same ranking analogue's Full search uses, so the filter
dropdown and the analogue candidates agree on what "class" means.

Default run fills only NULL drug_class rows (cheap, idempotent); pass --all to
refresh every row after a ranking change. The demo dataset does not need this
job: data/demo/drugs.csv commits the classes and seed_demo writes them.
"""

from __future__ import annotations

import sys

from medstock_shared import engine
from medstock_shared.models import Drug
from medstock_shared.rxnorm import RxNormError, primary_class_name
from sqlalchemy import select
from sqlalchemy.orm import Session


def run(refresh_all: bool = False) -> int:
    updated = 0
    with Session(engine) as s:
        query = select(Drug)
        if not refresh_all:
            query = query.where(Drug.drug_class.is_(None))
        for drug in s.scalars(query):
            rxcui = (drug.raw or {}).get("rxcui")
            if not rxcui:
                continue
            try:
                name = primary_class_name(str(rxcui))
            except RxNormError as exc:
                print(f"FAIL rxcui={rxcui} ndc={drug.ndc}: {exc}", file=sys.stderr)
                continue
            if name and name != drug.drug_class:
                drug.drug_class = name
                updated += 1
        s.commit()
    return updated


if __name__ == "__main__":
    print(f"drug_classes: updated {run(refresh_all='--all' in sys.argv[1:])} rows")
