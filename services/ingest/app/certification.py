"""CronJob entrypoint, daily: FDA approval + listing + recall status
-> drug_certification / certification_finding (COMP-1).

Two feeds, one pass. The NDC Directory gives the dates that decide whether a
product is still legally marketed; Enforcement gives open recalls. Both are
keyless openFDA JSON endpoints.

**Budget.** openFDA allows 1 000 requests/day *per IP* (docs/services.md §7) and
that budget is shared with every other feed and with COMP-2's on-demand
exploration. This job pages in bulk — 1 000 records per request, capped by
`MAX_PAGES` — rather than querying per drug.

ponytail: the response field names below follow openFDA's documented shape but
have not been verified against a live response. The natural keys (`ndc`, and
`code`+`source_ref` for findings) are what must stay stable; individual field
names may move. Do not point a real schedule at this until they are checked.
"""

from datetime import date

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from medstock_shared import engine
from medstock_shared.certification import (
    RULESET_VERSION,
    Recall,
    evaluate,
    parse_fda_date,
    status_for,
)
from medstock_shared.models import CertificationFinding, DrugCertification

from ._source import fetch_json

NDC_URL = "https://api.fda.gov/drug/ndc.json"
ENFORCEMENT_URL = "https://api.fda.gov/drug/enforcement.json"

PAGE_SIZE = 1000
MAX_PAGES = 20  # 20k products; raise only with the daily budget in mind
RECALL_PAGES = 5


def _pages(url: str, params: dict, max_pages: int) -> list[dict]:
    """openFDA pages with limit/skip and 404s an empty result set rather than
    returning `results: []`. A short page means the end."""
    out: list[dict] = []
    for page in range(max_pages):
        try:
            data = fetch_json(url, params={**params, "limit": PAGE_SIZE, "skip": page * PAGE_SIZE})
        except Exception:  # noqa: BLE001 — an exhausted page set is not a failure
            break
        results = data.get("results", [])
        out.extend(results)
        if len(results) < PAGE_SIZE:
            break
    return out


def _ndcs_of(record: dict) -> list[str]:
    """A recall names its products in several places depending on the feed
    version. Collect all of them and let the caller de-duplicate."""
    openfda = record.get("openfda") or {}
    found = list(openfda.get("package_ndc", [])) + list(openfda.get("product_ndc", []))
    if record.get("product_ndc"):
        found.append(record["product_ndc"])
    return [str(n) for n in found if n]


def recalls_by_ndc() -> dict[str, list[Recall]]:
    """Open drug recalls, indexed by every NDC they name."""
    index: dict[str, list[Recall]] = {}
    for row in _pages(ENFORCEMENT_URL, {"search": 'status:"Ongoing"'}, RECALL_PAGES):
        recall = Recall(
            classification=row.get("classification"),
            status=row.get("status"),
            recall_number=str(row.get("recall_number") or ""),
            reason=str(row.get("reason_for_recall") or ""),
            raw=row,
        )
        for ndc in _ndcs_of(row):
            index.setdefault(ndc, []).append(recall)
    return index


def _product_to_values(
    product: dict, recalls: list[Recall], today: date | None = None
) -> tuple[dict, list[dict]] | None:
    """One source record -> one certification row plus its findings.

    Returns `None` for a record with no usable NDC — there is nothing to key on.
    """
    ndc = product.get("product_ndc") or product.get("ndc")
    if not ndc:
        return None

    marketing_end = parse_fda_date(product.get("marketing_end_date"))
    listing_expiry = parse_fda_date(product.get("listing_expiration_date"))
    category = product.get("marketing_category")

    findings = evaluate(
        marketing_end_date=marketing_end,
        listing_expiration_date=listing_expiry,
        marketing_category=category,
        recalls=recalls,
        today=today,
    )

    certification = {
        "ndc": str(ndc),
        "status": str(status_for(findings)),
        "marketing_end_date": marketing_end,
        "listing_expiration_date": listing_expiry,
        "marketing_category": category,
        "application_number": product.get("application_number"),
        "labeler": product.get("labeler_name"),
        "provenance": "scheduled",
        "ruleset_version": RULESET_VERSION,
        "raw": product,
    }
    finding_rows = [
        {
            "ndc": str(ndc),
            "code": f.code,
            "severity": str(f.severity),
            "message": f.message,
            "source": f.source,
            "source_url": f.source_url,
            "source_ref": f.source_ref,
            "raw": f.raw,
        }
        for f in findings
    ]
    return certification, finding_rows


def run() -> int:
    recalls = recalls_by_ndc()
    products = _pages(NDC_URL, {}, MAX_PAGES)

    mapped = [
        row
        for row in (_product_to_values(p, recalls.get(str(p.get("product_ndc") or ""), [])) for p in products)
        if row is not None
    ]
    if not mapped:
        return 0

    with Session(engine) as s:
        for certification, findings in mapped:
            s.execute(
                insert(DrugCertification)
                .values(**certification)
                .on_conflict_do_update(
                    index_elements=["ndc"],
                    set_={k: v for k, v in certification.items() if k != "ndc"},
                )
            )
            # Findings are replaced, not merged: a terminated recall must stop
            # appearing, and a re-run must not accumulate duplicates.
            s.execute(
                delete(CertificationFinding).where(CertificationFinding.ndc == certification["ndc"])
            )
            if findings:
                s.execute(insert(CertificationFinding).values(findings))
        s.commit()
    return len(mapped)


if __name__ == "__main__":
    print(f"certification: upserted {run()} rows")
