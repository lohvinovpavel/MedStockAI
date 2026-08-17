"""CronJob entrypoint, daily: CMS NADAC reference pricing -> drug_price.

NADAC is a survey of what pharmacies actually **pay** for a drug. That is a
different number from Medicaid reimbursement, and it is the one worth quoting
next to a substitution: "the alternative costs 40% less" has to mean acquisition
cost, not a reimbursement figure that is gross of rebates.

Field names verified against live responses on 2026-08-14. The previous
`data.medicaid.gov/resource/…` Socrata URL returns **404** — that host moved to
a DKAN metastore, so a dataset is found by title and then queried by its
identifier. Datasets are annual, so "latest" means the newest year present.

  ndc                 11-digit, joins straight to stock_snapshot
  nadac_per_unit      acquisition cost per pricing_unit
  pricing_unit        EA | ML | GM  — a price is meaningless without it
  effective_date      weekly
  classification_for_rate_setting   B(rand) | G(eneric)
"""

from __future__ import annotations

from medstock_shared import engine
from medstock_shared.models import DrugPrice
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ._source import fetch_json

METASTORE = "https://data.medicaid.gov/api/1/metastore/schemas/dataset/items"
DATASTORE = "https://data.medicaid.gov/api/1/datastore/query/{ident}/0"
TITLE_PREFIX = "NADAC (National Average Drug Acquisition Cost)"

PAGE_SIZE = 5000
MAX_PAGES = 40  # ~200k rows; a full year is ~1M, so this is a bounded refresh


def latest_dataset() -> tuple[int, str] | None:
    """Newest annual NADAC dataset, as (year, identifier)."""
    items = fetch_json(METASTORE, params={"show-reference-ids": "false"})
    found: dict[int, str] = {}
    for item in items if isinstance(items, list) else []:
        title = str(item.get("title") or "")
        if title.startswith(TITLE_PREFIX):
            tail = title.split()[-1]
            if tail.isdigit():
                found[int(tail)] = item["identifier"]
    if not found:
        return None
    year = max(found)
    return year, found[year]


def _row_to_values(row: dict) -> dict | None:
    """`None` for a row with no usable key — NADAC carries occasional blanks."""
    ndc, effective = row.get("ndc"), row.get("effective_date")
    price = row.get("nadac_per_unit")
    if not ndc or not effective or price in (None, ""):
        return None
    try:
        unit_price = float(price)
    except (TypeError, ValueError):
        return None
    return {
        "ndc": str(ndc),
        "effective_date": str(effective)[:10],
        "unit_price": unit_price,
        # pricing_unit lives here rather than a column: $1.04 "per EA" and
        # "per ML" are not comparable, and the raw row is what a price
        # comparison has to be able to point at.
        "raw": row,
    }


def run(max_pages: int = MAX_PAGES) -> int:
    dataset = latest_dataset()
    if dataset is None:
        return 0
    _, ident = dataset

    seen: dict[tuple[str, str], dict] = {}
    for page in range(max_pages):
        data = fetch_json(
            DATASTORE.format(ident=ident),
            params={"limit": PAGE_SIZE, "offset": page * PAGE_SIZE},
        )
        results = data.get("results") or []
        for row in results:
            values = _row_to_values(row)
            if values:
                # One NDC can appear twice in a page across effective dates;
                # last one wins, and the upsert below settles the rest.
                seen[(values["ndc"], values["effective_date"])] = values
        if len(results) < PAGE_SIZE:
            break

    rows = list(seen.values())
    if not rows:
        return 0

    with Session(engine) as s:
        for values in rows:
            s.execute(
                insert(DrugPrice)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["ndc", "effective_date"],
                    set_={"unit_price": values["unit_price"], "raw": values["raw"]},
                )
            )
        s.commit()
    return len(rows)


if __name__ == "__main__":
    print(f"pricing: upserted {run()} rows")
