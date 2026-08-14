"""CronJob entrypoint, hourly: FDA drug shortages -> shortage_event.

ponytail: FEED_URL/field names are a placeholder — the real FDA Drug Shortage
feed's response shape hasn't been verified yet (openFDA does not currently
mirror this dataset; confirm the actual source before scheduling this for
real). The upsert shape (_row_to_values + ON CONFLICT on source_id) is what
matters and will not change once the real field names are swapped in.
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
