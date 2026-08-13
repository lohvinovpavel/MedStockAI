"""CronJob entrypoint, weekly: RxNorm (NLM) equivalence graph -> rxnorm_edge.

ponytail: same placeholder caveat as shortages.py — this hits RxNorm's
getRelatedByType per RXCUI, which needs a starting RXCUI list. That list
(the formulary's drugs, most likely) isn't decided; run() below just shows
the upsert shape against a single test RXCUI.
"""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from medstock_shared import engine
from medstock_shared.models import RxnormEdge

from ._source import fetch_json

FEED_URL = "https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/related.json"


def _row_to_values(rxcui_from: str, edge: dict) -> dict:
    return {
        "rxcui_from": rxcui_from,
        "rxcui_to": edge["rxcui"],
        "relationship": edge.get("tty", "unknown"),
        "raw": edge,
    }


def run(rxcuis: list[str]) -> int:
    total = 0
    with Session(engine) as s:
        for rxcui_from in rxcuis:
            data = fetch_json(FEED_URL.format(rxcui=rxcui_from))
            groups = data.get("relatedGroup", {}).get("conceptGroup", []) or []
            for group in groups:
                for concept in group.get("conceptProperties", []) or []:
                    values = _row_to_values(rxcui_from, concept)
                    s.execute(
                        insert(RxnormEdge)
                        .values(**values)
                        .on_conflict_do_update(
                            index_elements=["rxcui_from", "rxcui_to", "relationship"],
                            set_={"raw": values["raw"]},
                        )
                    )
                    total += 1
        s.commit()
    return total


if __name__ == "__main__":
    # TODO: source this from the union of formulary_item.rxcui, not a stub list.
    print(f"rxnorm: upserted {run(rxcuis=[])} rows")
