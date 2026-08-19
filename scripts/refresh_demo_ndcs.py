"""Repoint demo NDCs at packages openFDA still lists.

The demo catalogue's NDCs are not invented -- every one of them is a genuine
RxNorm NDC for the drug it names. They are *delisted*: openFDA's NDC Directory
carries currently marketed packages, and a package that has been discontinued
falls out of it. Certification asks the directory, gets nothing back, and writes
no row, so the compliance badge reads Unknown forever.

Measured before this script: 64 of 100 catalogue NDCs resolved. The other 36
could never certify no matter how often the pass ran.

The replacement is picked by RxCUI, not by name. RxNav answers
`/rxcui/{id}/ndcs` with every NDC for that exact clinical concept -- same
ingredient, same strength, same dose form -- so any of them denotes the same
product a pharmacist would recognise. From that list this takes the packages
openFDA currently lists and picks the lowest, which is arbitrary but stable:
re-running must not churn the seed data.

Matching is done on the *package* NDC, not the product NDC, because that is
what certification keys its rows by (`_product_to_rows` -> `ndc11(package_ndc)`).
A product that resolves while the specific package does not would still leave
the badge blank, so checking `product_ndc` alone would report success and
change nothing.

    python scripts/refresh_demo_ndcs.py            # report only, writes nothing
    python scripts/refresh_demo_ndcs.py --write    # rewrite drugs.csv + regenerate series
    python scripts/refresh_demo_ndcs.py --check    # exit 1 if any NDC is stale
"""

from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "ingest"))

from app._source import fetch_json
from app.certification import NDC_URL, PAGE_SIZE, _product_ndcs, _quote, product_ndc_candidates
from medstock_shared.certification import ndc11

DRUGS_CSV = Path(__file__).resolve().parents[1] / "data" / "demo" / "drugs.csv"
RXNAV = "https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/ndcs.json"

# One request per drug rather than per NDC. The cap is the query string, not the
# result set (limit is already PAGE_SIZE), and 30 NDCs expand to ~60 OR-terms.
BATCH = 30

# RxNav returns every NDC a concept has ever had -- for common generics that is
# several hundred, and checking all of them turned this script into ~360
# sequential requests and ten minutes. Two batches per drug is enough to find a
# listed package in practice, and bounds the whole run to ~70 requests.
MAX_CANDIDATES = 2 * BATCH

# The requests are independent and entirely latency-bound, so they overlap.
# Kept modest: openFDA allows 240 requests/minute anonymously and there is no
# reason to spend that budget faster than the work needs.
WORKERS = 8


def _as_ndc11(value: str) -> str | None:
    """11-digit form, or None if the directory handed back something unparseable.

    openFDA occasionally carries malformed packaging entries; one bad string
    should drop that package, not abort the pass.
    """
    try:
        return ndc11(str(value))
    except (ValueError, TypeError):
        return None


def listed_package_ndcs(ndcs: list[str]) -> set[str]:
    """The subset openFDA currently lists, as 11-digit package NDCs.

    Returns packages rather than products deliberately -- see the module note.
    """
    found: set[str] = set()
    for i in range(0, len(ndcs), BATCH):
        chunk = ndcs[i : i + BATCH]
        terms = [_quote(c) for n in chunk for c in product_ndc_candidates(n)]
        if not terms:
            continue
        try:
            data = fetch_json(
                NDC_URL,
                params={"search": "product_ndc:(" + " OR ".join(terms) + ")", "limit": PAGE_SIZE},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                continue  # nothing in this chunk is listed
            raise
        for product in data.get("results", []):
            found.update(n for n in map(_as_ndc11, _product_ndcs(product)) if n)
    return found


def rxnav_ndcs(rxcui: str) -> list[str]:
    """Every NDC RxNorm holds for this concept, newest-agnostic and sorted.

    Sorted so the pick below is reproducible; RxNav's own ordering is not
    documented as stable.
    """
    try:
        data = fetch_json(RXNAV.format(rxcui=rxcui))
    except httpx.HTTPError:
        return []
    group = (data or {}).get("ndcGroup") or {}
    ndc_list = (group.get("ndcList") or {}).get("ndc") or []
    return sorted({str(n).strip() for n in ndc_list if str(n).strip()})


def _replacement_for(row: dict) -> tuple[str, str | None]:
    """(old ndc, replacement or None) for one stale row. Safe to run in a thread:
    it touches no shared state and every call it makes is a plain HTTP GET."""
    ndc = (row.get("ndc") or "").strip()
    rxcui = (row.get("rxcui") or "").strip()
    if not rxcui:
        return ndc, None
    candidates = [c for c in rxnav_ndcs(rxcui) if c != ndc][:MAX_CANDIDATES]
    if not candidates:
        return ndc, None
    usable = sorted(listed_package_ndcs(candidates) & set(candidates))
    return ndc, (usable[0] if usable else None)


def resolve(rows: list[dict]) -> tuple[dict[str, str], list[dict]]:
    """(old ndc -> new ndc) for stale rows, plus the rows nothing could fix."""
    current = [r["ndc"] for r in rows if r.get("ndc")]
    listed = listed_package_ndcs(current)

    stale = [r for r in rows if (r.get("ndc") or "").strip() and r["ndc"].strip() not in listed]
    remap: dict[str, str] = {}
    unfixable: list[dict] = []
    by_ndc = {(r.get("ndc") or "").strip(): r for r in stale}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for ndc, replacement in pool.map(_replacement_for, stale):
            if replacement:
                remap[ndc] = replacement
            else:
                unfixable.append(by_ndc[ndc])
    return remap, unfixable


def regenerate() -> None:
    """Rebuild the generated demo series from the rewritten catalogue.

    The consumption history, the forecast fitted to it, the shelf and the stock
    tail all key on NDC, so repointing drugs.csv alone orphans every one of
    them. Regeneration, not substitution: `gen_demo` derives its per-drug
    randomness from the catalogue row, so rewriting the ndc column in place
    produces files the generator would never emit --
    `test_regeneration_reproduces_committed_artifacts` exists to catch exactly
    that, and did.

    Deterministic (same seed, gzip mtime pinned to 0), so this is reproducible
    and the diff is real content rather than a timestamp.
    """
    from app import gen_demo  # imported here: it reads DRUGS_CSV at call time

    gen_demo.run()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite data/demo/drugs.csv")
    parser.add_argument("--check", action="store_true", help="exit 1 if any NDC is stale")
    args = parser.parse_args()

    with DRUGS_CSV.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    remap, unfixable = resolve(rows)

    print(f"catalogue rows       : {len(rows)}")
    print(f"already certifiable  : {len(rows) - len(remap) - len(unfixable)}")
    print(f"repointed            : {len(remap)}")
    print(f"no listed package    : {len(unfixable)}")
    for row in unfixable:
        print(f"  UNFIXABLE {row.get('ndc')}  rxcui={row.get('rxcui')}  {row.get('name', '')[:44]}")
    for old, new in sorted(remap.items()):
        name = next((r.get("name", "") for r in rows if r.get("ndc") == old), "")
        print(f"  {old} -> {new}  {name[:44]}")

    if args.check:
        stale = len(remap) + len(unfixable)
        if stale:
            print(f"\n{stale} catalogue NDC(s) are not listed by openFDA and cannot certify.")
        return 1 if stale else 0

    if args.write and remap:
        for row in rows:
            if row.get("ndc") in remap:
                row["ndc"] = remap[row["ndc"]]
        with DRUGS_CSV.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote {DRUGS_CSV} ({len(remap)} NDCs repointed)")
        regenerate()
        print("regenerated the demo series from the new catalogue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
