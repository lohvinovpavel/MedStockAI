"""CronJob entrypoint, hourly: FDA drug shortages -> shortage_event.

The upsert shape (_row_to_values + ON CONFLICT on source_id) is the join
surface B3 needs. The live FDA Drug Shortage feed URL/field names are still
unverified (openFDA does not currently mirror this dataset). Until that feed
is confirmed, the demo plants the three mock-aligned rows (Norepinephrine,
Ceftriaxone, Heparin) from `demo_shelf.demo_shortage_rows()` during seed_demo
/ seed_stock so exposure `uncovered` is a real claim rather than an empty join.
"""

from medstock_shared import engine
from medstock_shared.models import ShortageEvent
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ._source import fetch_json

FEED_URL = "https://api.fda.gov/drug/shortages.json"  # TODO: verify against the real feed


def _row_to_values(row: dict) -> dict:
    return {
        "source_id": str(row.get("shortage_id") or row.get("id") or row["ndc"]),
        "ndc": row.get("ndc"),
        "status": row.get("status"),
        "raw": row,
    }


def run() -> int:
    data = fetch_json(FEED_URL, params={"limit": 1000})
    rows = [_row_to_values(r) for r in data.get("results", [])]
    if not rows:
        return 0
    with Session(engine) as s:
        for values in rows:
            s.execute(
                insert(ShortageEvent)
                .values(**values)
                .on_conflict_do_update(index_elements=["source_id"], set_=values)
            )
        s.commit()
    return len(rows)


if __name__ == "__main__":
    print(f"shortages: upserted {run()} rows")
