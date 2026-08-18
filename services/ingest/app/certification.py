"""CronJob entrypoint, daily: FDA approval + listing + recall status
-> drug_certification / certification_finding (COMP-1).

Two feeds, one pass. The NDC Directory gives the dates that decide whether a
product is still legally marketed; Enforcement gives open recalls.  Both are
keyless openFDA JSON endpoints.

Field names here were verified against live responses on 2026-08-14 — they are
not placeholders. What the probes established, because each one shaped the code:

* **`skip` is capped at 25 000.** Past that openFDA answers `400`, and past the
  end of a result set it answers `404`. So the directory (136 942 products,
  115 306 of them finished) *cannot* be fully synced by paging. Bulk coverage
  needs openFDA's download endpoint; this job deliberately does the bounded
  thing instead — see `run()`.
* **Only 1 033 of 2 630 ongoing recalls carry `openfda.package_ndc`.** The other
  61% name their product in `product_description` free text only, and cannot be
  joined to an NDC by any deterministic rule. This job filters to the joinable
  ones rather than pretending; extracting identity from that free text is COMP-2's
  `extract` task, and this is the concrete reason it exists.
* Recall payloads carry `classification`, `status`, `recall_number` and
  `reason_for_recall` at the top level, with NDCs nested under `openfda`.

**Budget.** openFDA allows 1 000 requests/day per IP (docs/services.md §7),
shared with every other feed. This job reads `meta.results.total` from the first
page and stops there, so a normal run costs a handful of requests and never
spends any on a 404.
"""

from datetime import date

import httpx
from medstock_shared import engine
from medstock_shared.certification import (
    RULESET_VERSION,
    AlertListing,
    Recall,
    Shortage,
    evaluate,
    firm_key,
    ndc11,
    parse_fda_date,
    product_ndc_candidates,
    status_for,
)
from medstock_shared.db import SessionLocal
from medstock_shared.models import (
    CertificationFinding,
    DrugCertification,
    ImportAlert,
    StockSnapshot,
)
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

from ._source import fetch_json

NDC_URL = "https://api.fda.gov/drug/ndc.json"
ENFORCEMENT_URL = "https://api.fda.gov/drug/enforcement.json"
SHORTAGE_URL = "https://api.fda.gov/drug/shortages.json"

PAGE_SIZE = 1000
SKIP_MAX = 25_000  # openFDA answers 400 above this
DIRECTORY_PAGES = 20  # bounded sweep when no NDC list is given

# Only recalls that can actually be joined to a product. Without this the job
# pages through ~1 600 records it can do nothing with.
ONGOING_JOINABLE = 'status:"Ongoing" AND _exists_:openfda.package_ndc'


def _pages(url: str, params: dict, max_pages: int) -> list[dict]:
    """Page until the result set is exhausted, `max_pages`, or openFDA's skip
    ceiling — whichever comes first.

    `meta.results.total` on the first response tells us how far to go, so the
    common path never provokes the end-of-results 404. A 404 is still tolerated
    (the total can move between pages); anything else propagates, because a
    swallowed timeout would truncate the feed and then write a complete-looking
    result for a fraction of the catalogue.
    """
    out: list[dict] = []
    total: int | None = None

    for page in range(max_pages):
        skip = page * PAGE_SIZE
        if skip > SKIP_MAX or (total is not None and skip >= total):
            break
        try:
            data = fetch_json(url, params={**params, "limit": PAGE_SIZE, "skip": skip})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                break  # past the last page
            raise
        if total is None:
            total = data.get("meta", {}).get("results", {}).get("total")
        results = data.get("results", [])
        out.extend(results)
        if len(results) < PAGE_SIZE:
            break
    return out


def _ndcs_of(record: dict) -> list[str]:
    """Every NDC a recall names. Verified: these live under `openfda`, and are
    absent entirely on 61% of ongoing recalls."""
    openfda = record.get("openfda") or {}
    found = list(openfda.get("package_ndc", [])) + list(openfda.get("product_ndc", []))
    if record.get("product_ndc"):
        found.append(record["product_ndc"])
    return [str(n) for n in found if n]


def _product_ndcs(product: dict) -> list[str]:
    """Every NDC a directory record answers to: the product NDC plus each
    package NDC under it.

    Both are needed for the recall join — enforcement annotates products by
    package NDC, so matching on `product_ndc` alone drops the join entirely.
    """
    found = [product.get("product_ndc") or product.get("ndc")]
    for pack in product.get("packaging") or []:
        if isinstance(pack, dict) and pack.get("package_ndc"):
            found.append(pack["package_ndc"])
    return [str(n) for n in found if n]


def import_alerts_for(labeler: str | None) -> list[AlertListing]:
    """Red List entries matching this labeler, or none.

    Reads its own session because this module is a batch job, not a request
    handler. A missing table degrades to no listings: the openFDA findings are
    the backbone of a badge and must stay computable before the weekly scrape
    has ever run.
    """
    if not labeler:
        return []
    try:
        with SessionLocal() as session:
            rows = session.scalars(
                select(ImportAlert).where(ImportAlert.firm_key == firm_key(labeler))
            ).all()
            return [
                AlertListing(
                    alert_number=r.alert_number,
                    firm_name=r.firm_name,
                    country=r.country or "",
                    listed_at=r.listed_at,
                    source_url=r.source_url or "",
                )
                for r in rows
            ]
    except (ProgrammingError, SQLAlchemyError):
        return []


def _package_ndcs(product: dict) -> list[str]:
    """Just the package NDCs — what a certification row is keyed by.

    Distinct from `_product_ndcs`, which also includes the product NDC for the
    recall join. A product NDC has no package segment, so normalising it gives a
    9-digit value that can never match an inventory row; keying on it would
    create a row nothing ever reads.
    """
    return [
        str(pack["package_ndc"])
        for pack in product.get("packaging") or []
        if isinstance(pack, dict) and pack.get("package_ndc")
    ]


def _recalls_for(product: dict, index: dict[str, list[Recall]]) -> list[Recall]:
    """Open recalls against any of this product's NDCs, de-duplicated — one
    recall listing several package sizes is still one recall."""
    seen: dict[str, Recall] = {}
    for ndc in _product_ndcs(product):
        for recall in index.get(ndc, []):
            # Recalls without a number cannot be keyed; fall back to identity so
            # two distinct ones are not collapsed into one.
            seen[recall.recall_number or f"id:{id(recall)}"] = recall
    return list(seen.values())


def recalls_by_ndc() -> dict[str, list[Recall]]:
    """Open, joinable drug recalls indexed by every NDC they name."""
    index: dict[str, list[Recall]] = {}
    for row in _pages(ENFORCEMENT_URL, {"search": ONGOING_JOINABLE}, max_pages=5):
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


def shortages_by_ndc() -> dict[str, list[Shortage]]:
    """Active FDA drug shortages, indexed by canonical 11-digit package NDC.

    Verified live: every record carries `package_ndc`, so unlike recalls this
    feed joins completely. Note it is keyed per *package*, not per product —
    one pack size can be in shortage while another is not.
    """
    index: dict[str, list[Shortage]] = {}
    for row in _pages(SHORTAGE_URL, {}, max_pages=5):
        ndc = row.get("package_ndc")
        if not ndc:
            continue
        categories = row.get("therapeutic_category") or []
        index.setdefault(ndc11(str(ndc)), []).append(
            Shortage(
                status=row.get("status"),
                generic_name=str(row.get("generic_name") or ""),
                therapeutic_category=", ".join(str(c) for c in categories)
                if isinstance(categories, list)
                else str(categories),
                update_date=str(row.get("update_date") or ""),
                raw=row,
            )
        )
    return index


def _quote(ndc: str) -> str:
    return '"' + ndc.replace('"', "") + '"'


def products_for_ndcs(ndcs: list[str], batch: int = 12) -> list[dict]:
    """Directory records for a set of 11-digit NDCs.

    Each one expands to its plausible hyphenations (`product_ndc_candidates`)
    because openFDA is not searchable by the 11-digit form. Batches stay small:
    every NDC contributes two or three OR-terms to the query string.

    This is the targeted path — certifying the drugs actually on a shelf costs a
    handful of requests, where sweeping the directory cannot reach past 25 000
    records however many it spends.
    """
    out: list[dict] = []
    for i in range(0, len(ndcs), batch):
        terms = [_quote(c) for n in ndcs[i : i + batch] for c in product_ndc_candidates(n)]
        search = "product_ndc:(" + " OR ".join(terms) + ")"
        try:
            data = fetch_json(NDC_URL, params={"search": search, "limit": PAGE_SIZE})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                continue  # nothing in this chunk is in the directory — legitimately Unknown
            raise
        out.extend(data.get("results", []))
    return out


def _product_to_rows(
    product: dict,
    recalls: list[Recall],
    shortages: dict[str, list[Shortage]] | None = None,
    today: date | None = None,
) -> list[tuple[dict, list[dict]]]:
    """One source record -> one certification row **per package NDC**.

    A directory record describes a product; inventory holds package NDCs, so the
    badge has to be findable by the NDC on the shelf. Approval and recalls apply
    to the whole product, but **shortages are declared per package** — one pack
    size can be short while another is fine — so each key is evaluated
    separately rather than sharing one status.

    Returns `[]` for a record with no usable NDC: there is nothing to key on.
    """
    # Packages first; fall back to the product NDC only when the record lists no
    # packaging at all, so such a product is still recorded rather than dropped.
    packages = _package_ndcs(product)
    source = packages or [n for n in [product.get("product_ndc") or product.get("ndc")] if n]
    keys = sorted({ndc11(str(n)) for n in source})
    if not keys:
        return []

    shortages = shortages or {}
    common = {
        "marketing_end_date": parse_fda_date(product.get("marketing_end_date")),
        "marketing_start_date": parse_fda_date(product.get("marketing_start_date")),
        "listing_expiration_date": parse_fda_date(product.get("listing_expiration_date")),
        "marketing_category": product.get("marketing_category"),
        "finished": product.get("finished"),
    }

    # Import-alert listings for this product's labeler, matched exactly on the
    # normalised firm name — see certification.firm_key for why never fuzzy.
    # The scheduled path applies the same rule as the on-demand one so a badge
    # does not change colour depending on which produced it.
    listings = import_alerts_for(product.get("labeler_name"))

    rows: list[tuple[dict, list[dict]]] = []
    for key in keys:
        findings = evaluate(
            **common,
            recalls=recalls,
            shortages=shortages.get(key, ()),
            import_alerts=listings,
            today=today,
        )
        certification = {
            "ndc": key,
            "status": str(status_for(findings)),
            "marketing_end_date": common["marketing_end_date"],
            "listing_expiration_date": common["listing_expiration_date"],
            "marketing_category": common["marketing_category"],
            "application_number": product.get("application_number"),
            "labeler": product.get("labeler_name"),
            "provenance": "scheduled",
            "ruleset_version": RULESET_VERSION,
            "raw": product,
        }
        finding_rows = [
            {
                "ndc": key,
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
        rows.append((certification, finding_rows))
    return rows


def shelf_ndcs() -> list[str]:
    """Product NDCs anyone actually stocks. Reference data with a working set."""
    with Session(engine) as s:
        return sorted({str(n) for n in s.scalars(select(StockSnapshot.ndc).distinct()).all()})


def write(mapped: list[tuple[dict, list[dict]]]) -> int:
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


def run(targeted: bool = True) -> int:
    """Targeted by default: certify what is on a shelf.

    The alternative — sweeping the whole directory — cannot complete anyway
    (§ module docstring: `skip` stops at 25 000 against 136 942 products), so a
    full sweep is a partial sweep with a bigger bill. Pass `targeted=False` to
    take the bounded sweep regardless; a real full sync needs openFDA's bulk
    download endpoint, which is the documented next step.
    """
    recalls = recalls_by_ndc()
    shortages = shortages_by_ndc()

    if targeted:
        wanted = shelf_ndcs()
        if not wanted:
            return 0
        products = products_for_ndcs(wanted)
    else:
        products = _pages(NDC_URL, {"search": "finished:true"}, DIRECTORY_PAGES)

    mapped = [
        row
        for p in products
        for row in _product_to_rows(p, _recalls_for(p, recalls), shortages)
    ]
    return write(mapped) if mapped else 0


if __name__ == "__main__":
    import sys

    full = "--full" in sys.argv
    print(f"certification: upserted {run(targeted=not full)} rows")
