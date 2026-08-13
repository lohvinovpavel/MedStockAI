"""CronJob entrypoint, daily: CMS NADAC reference pricing -> drug_price.

ponytail: same placeholder caveat as shortages.py — verify the Socrata
dataset id and field names before scheduling for real.
"""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from medstock_shared import engine
from medstock_shared.models import DrugPrice

from ._source import fetch_json

# TODO: confirm dataset id — CMS NADAC is published via Socrata (data.medicaid.gov)
FEED_URL = "https://data.medicaid.gov/resource/nadac-national-average-drug-acquisition-cost.json"


def _row_to_values(row: dict) -> dict:
    return {
        "ndc": row["ndc"],
        "effective_date": row["effective_date"],
        "unit_price": row.get("nadac_per_unit"),
        "raw": row,
    }


def run() -> int:
    data = fetch_json(FEED_URL, params={"$limit": 5000})
    rows = [_row_to_values(r) for r in data]
    if not rows:
        return 0
    with Session(engine) as s:
        for values in rows:
            s.execute(
                insert(DrugPrice)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["ndc", "effective_date"], set_={"unit_price": values["unit_price"], "raw": values["raw"]}
                )
            )
        s.commit()
    return len(rows)


if __name__ == "__main__":
    print(f"pricing: upserted {run()} rows")
