"""One-time builder for data/demo/drugs.csv — the real-drug list behind the
demo generator (tasks/mykhailo-chaus/001, issue #8).

Resolves each curated drug name to a real RxCUI (SCD/SBD) and NDC via RxNav,
then writes the reviewed CSV that gen_demo/seed_demo consume. Network runs
here, once; nothing at generation or seed time calls RxNav. Rerun only to
change the list, and commit the resulting CSV.

    uv run --no-sync python scripts/build_demo_drugs.py

Columns: ndc, rxcui, name (RxNorm canonical), query_name, cohort,
storage_class, storage_min_c, storage_max_c, humidity_max_pct, base_daily,
stockout_prone, drug_class (primary RxClass name; blank when NLM has none).

Cohorts (the generator's contract with prediction, issue #7):
  flat          — steady chronic-care demand
  winter        — respiratory/flu season peak (Dec–Feb)
  summer        — allergy/UTI season peak (May–Aug)
  trending_up   — demand grows year over year (GLP-1s, DOACs)
  trending_down — demand shrinks year over year (warfarin era)
"""

from __future__ import annotations

import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

from medstock_shared.rxnorm import (
    RxNormError,
    ndcs_for_rxcui,
    primary_class_name,
    search_concepts,
)

OUT = ROOT / "data" / "demo" / "drugs.csv"

# storage_class → (min °C, max °C, max %RH). Class-level per USP, not parsed
# from SPL labels — see Drug model comment in medstock_shared/models.py.
STORAGE = {
    "crt": (15.0, 25.0, 60.0),
    "refrigerated": (2.0, 8.0, 75.0),
    "freezer": (-25.0, -15.0, 75.0),
}

# (query_name, cohort, storage_class, base_daily units, stockout_prone)
DRUGS: list[tuple[str, str, str, int, bool]] = [
    # --- flat: chronic-care staples, controlled room temp
    ("metformin 500 MG Oral Tablet", "flat", "crt", 120, False),
    ("lisinopril 10 MG Oral Tablet", "flat", "crt", 90, False),
    ("amlodipine 5 MG Oral Tablet", "flat", "crt", 85, False),
    ("atorvastatin 20 MG Oral Tablet", "flat", "crt", 100, False),
    ("rosuvastatin 10 MG Oral Tablet", "flat", "crt", 60, False),
    ("levothyroxine sodium 0.05 MG Oral Tablet", "flat", "crt", 70, False),
    ("omeprazole 20 MG Delayed Release Oral Capsule", "flat", "crt", 95, False),
    ("pantoprazole 40 MG Delayed Release Oral Tablet", "flat", "crt", 80, False),
    ("famotidine 20 MG Oral Tablet", "flat", "crt", 50, False),
    ("sertraline 50 MG Oral Tablet", "flat", "crt", 55, False),
    ("escitalopram 10 MG Oral Tablet", "flat", "crt", 50, False),
    ("fluoxetine 20 MG Oral Capsule", "flat", "crt", 45, False),
    ("quetiapine 100 MG Oral Tablet", "flat", "crt", 30, False),
    ("olanzapine 10 MG Oral Tablet", "flat", "crt", 25, False),
    ("lorazepam 1 MG Oral Tablet", "flat", "crt", 35, False),
    ("alprazolam 0.5 MG Oral Tablet", "flat", "crt", 30, False),
    ("gabapentin 300 MG Oral Capsule", "flat", "crt", 65, False),
    ("pregabalin 75 MG Oral Capsule", "flat", "crt", 40, False),
    ("hydrochlorothiazide 25 MG Oral Tablet", "flat", "crt", 55, False),
    ("furosemide 40 MG Oral Tablet", "flat", "crt", 70, False),
    ("spironolactone 25 MG Oral Tablet", "flat", "crt", 40, False),
    ("metoprolol tartrate 50 MG Oral Tablet", "flat", "crt", 85, False),
    ("carvedilol 12.5 MG Oral Tablet", "flat", "crt", 45, False),
    ("losartan potassium 50 MG Oral Tablet", "flat", "crt", 75, False),
    ("valsartan 80 MG Oral Tablet", "flat", "crt", 40, False),
    ("clopidogrel 75 MG Oral Tablet", "flat", "crt", 60, False),
    ("aspirin 81 MG Oral Tablet", "flat", "crt", 110, False),
    ("digoxin 0.125 MG Oral Tablet", "flat", "crt", 15, False),
    ("allopurinol 300 MG Oral Tablet", "flat", "crt", 35, False),
    ("prednisone 10 MG Oral Tablet", "flat", "crt", 50, False),
    ("ibuprofen 600 MG Oral Tablet", "flat", "crt", 90, False),
    ("acetaminophen 500 MG Oral Tablet", "flat", "crt", 150, False),
    ("naproxen 500 MG Oral Tablet", "flat", "crt", 55, False),
    ("tramadol hydrochloride 50 MG Oral Tablet", "flat", "crt", 45, False),
    ("oxycodone hydrochloride 5 MG Oral Tablet", "flat", "crt", 30, False),
    ("ondansetron 4 MG Oral Tablet", "flat", "crt", 60, False),
    ("metoclopramide 10 MG Oral Tablet", "flat", "crt", 30, False),
    ("tamsulosin hydrochloride 0.4 MG Oral Capsule", "flat", "crt", 35, False),
    ("finasteride 5 MG Oral Tablet", "flat", "crt", 20, False),
    ("sitagliptin 100 MG Oral Tablet", "flat", "crt", 30, False),
    ("glipizide 10 MG Oral Tablet", "flat", "crt", 25, False),
    ("duloxetine 60 MG Delayed Release Oral Capsule", "flat", "crt", 35, False),
    ("trazodone 50 MG Oral Tablet", "flat", "crt", 40, False),
    ("zolpidem tartrate 10 MG Oral Tablet", "flat", "crt", 25, False),
    ("atenolol 50 MG Oral Tablet", "flat", "crt", 30, False),
    ("clonidine 0.1 MG Oral Tablet", "flat", "crt", 20, False),
    ("potassium chloride 20 MEQ Extended Release Oral Tablet", "flat", "crt", 40, False),
    ("folic acid 1 MG Oral Tablet", "flat", "crt", 30, False),
    ("ferrous sulfate 325 MG Oral Tablet", "flat", "crt", 35, False),
    ("cholecalciferol 25 MCG Oral Tablet", "flat", "crt", 30, False),
    ("methylprednisolone 4 MG Oral Tablet", "flat", "crt", 20, False),
    ("ciprofloxacin 500 MG Oral Tablet", "flat", "crt", 30, False),
    ("vancomycin 1000 MG Injection", "flat", "crt", 20, False),
    ("heparin sodium 5000 UNT/ML Injectable Solution", "flat", "crt", 40, False),
    ("enoxaparin sodium 40 MG in 0.4 ML Prefilled Syringe", "flat", "crt", 25, False),
    # --- winter: respiratory / flu season
    ("oseltamivir 75 MG Oral Capsule", "winter", "crt", 40, False),
    ("amoxicillin 500 MG Oral Capsule", "winter", "crt", 80, True),
    ("amoxicillin 875 MG / clavulanate potassium 125 MG Oral Tablet", "winter", "crt", 45, False),
    ("azithromycin 250 MG Oral Tablet", "winter", "crt", 60, False),
    ("clarithromycin 500 MG Oral Tablet", "winter", "crt", 25, False),
    ("doxycycline hyclate 100 MG Oral Capsule", "winter", "crt", 40, False),
    ("cefuroxime axetil 250 MG Oral Tablet", "winter", "crt", 30, False),
    ("cephalexin 500 MG Oral Capsule", "winter", "crt", 45, False),
    ("levofloxacin 500 MG Oral Tablet", "winter", "crt", 35, False),
    ("ceftriaxone 1000 MG Injection", "winter", "crt", 30, False),
    ("benzonatate 100 MG Oral Capsule", "winter", "crt", 35, False),
    ("guaifenesin 400 MG Oral Tablet", "winter", "crt", 30, False),
    ("Ventolin HFA 0.09 MG/ACTUAT Metered Dose Inhaler", "winter", "crt", 50, True),
    ("ipratropium bromide 0.2 MG/ML Inhalation Solution", "winter", "crt", 20, False),
    ("Fluzone 2025-2026", "winter", "refrigerated", 30, False),
    # --- summer: allergy / UTI season
    ("cetirizine hydrochloride 10 MG Oral Tablet", "summer", "crt", 50, False),
    ("loratadine 10 MG Oral Tablet", "summer", "crt", 45, False),
    ("fexofenadine hydrochloride 180 MG Oral Tablet", "summer", "crt", 35, False),
    ("diphenhydramine hydrochloride 25 MG Oral Capsule", "summer", "crt", 40, False),
    ("fluticasone propionate 0.05 MG/ACTUAT Nasal Spray", "summer", "crt", 30, False),
    ("epinephrine 1 MG/ML Auto-Injector", "summer", "crt", 10, False),
    ("montelukast 10 MG Oral Tablet", "summer", "crt", 45, False),
    ("hydrocortisone 1 % Topical Cream", "summer", "crt", 25, False),
    ("nitrofurantoin 100 MG Oral Capsule", "summer", "crt", 25, False),
    # --- trending up: GLP-1s and DOACs displacing older therapy
    ("Ozempic 2 MG/1.5 ML Pen Injector", "trending_up", "refrigerated", 12, True),
    ("Mounjaro 5 MG per 0.5 ML Auto-Injector", "trending_up", "refrigerated", 8, False),
    ("Trulicity 1.5 MG per 0.5 ML Auto-Injector", "trending_up", "refrigerated", 8, False),
    ("Victoza 6 MG/ML Pen Injector", "trending_up", "refrigerated", 8, False),
    ("Jardiance 10 MG Oral Tablet", "trending_up", "crt", 30, False),
    ("apixaban 5 MG Oral Tablet", "trending_up", "crt", 55, False),
    ("rivaroxaban 20 MG Oral Tablet", "trending_up", "crt", 35, False),
    ("sacubitril 49 MG / valsartan 51 MG Oral Tablet", "trending_up", "crt", 20, False),
    # --- trending down: displaced by the drugs above
    ("warfarin sodium 5 MG Oral Tablet", "trending_down", "crt", 25, False),
    ("simvastatin 20 MG Oral Tablet", "trending_down", "crt", 30, False),
    # --- refrigerated cold chain (flat demand)
    ("insulin glargine 100 UNT/ML Pen Injector", "flat", "refrigerated", 25, False),
    ("insulin lispro 100 UNT/ML Pen Injector", "flat", "refrigerated", 20, False),
    ("insulin aspart 100 UNT/ML Pen Injector", "flat", "refrigerated", 20, False),
    ("Humulin N 100 UNT/ML Injectable Suspension", "flat", "refrigerated", 12, False),
    ("Humulin R 100 UNT/ML Injectable Solution", "flat", "refrigerated", 15, False),
    ("latanoprost 0.05 MG/ML Ophthalmic Solution", "flat", "refrigerated", 10, False),
    ("Epogen 4000 UNT/ML Injectable Solution", "flat", "refrigerated", 6, False),
    ("succinylcholine chloride 20 MG/ML Injectable Solution", "flat", "refrigerated", 8, False),
    ("rocuronium bromide 10 MG/ML Injectable Solution", "flat", "refrigerated", 10, False),
    # --- freezer
    ("Varivax 0.5 ML Injection", "flat", "freezer", 5, False),
    ("ProQuad", "flat", "freezer", 3, False),
]


def resolve(entry: tuple[str, str, str, int, bool]) -> dict | None:
    query, cohort, storage_class, base_daily, stockout_prone = entry
    try:
        hits = search_concepts(query, limit=12)
    except RxNormError as exc:
        print(f"FAIL search {query!r}: {exc}", file=sys.stderr)
        return None
    if not hits:
        print(f"FAIL search {query!r}: no SCD/SBD hit", file=sys.stderr)
        return None
    # Generic SCDs sometimes carry no marketed US NDC while the branded SBD
    # does — walk the hits in rank order and take the first with a real pack.
    hit, ndcs = None, []
    for candidate in hits:
        try:
            found = ndcs_for_rxcui(candidate["rxcui"])
        except RxNormError as exc:
            print(f"FAIL ndcs {query!r} ({candidate['rxcui']}): {exc}", file=sys.stderr)
            return None
        if found:
            hit, ndcs = candidate, found
            break
    if hit is None:
        print(f"FAIL ndcs {query!r} ({hits[0]['rxcui']} {hits[0]['name']}): none", file=sys.stderr)
        return None
    lo, hi, rh = STORAGE[storage_class]
    try:
        drug_class = primary_class_name(hit["rxcui"]) or ""
    except RxNormError as exc:
        print(f"WARN class {query!r} ({hit['rxcui']}): {exc}", file=sys.stderr)
        drug_class = ""
    return {
        "ndc": min(ndcs),  # deterministic pick across reruns
        "rxcui": hit["rxcui"],
        "name": hit["name"],
        "query_name": query,
        "cohort": cohort,
        "storage_class": storage_class,
        "storage_min_c": lo,
        "storage_max_c": hi,
        "humidity_max_pct": rh,
        "base_daily": base_daily,
        "stockout_prone": stockout_prone,
        "drug_class": drug_class,
    }


def main() -> int:
    rows: list[dict] = []
    failures = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(resolve, entry): entry for entry in DRUGS}
        for fut in as_completed(futures):
            row = fut.result()
            if row is None:
                failures += 1
            else:
                rows.append(row)

    # One NDC may back two queries (rare); keep the first by query order.
    by_ndc: dict[str, dict] = {}
    order = {entry[0]: i for i, entry in enumerate(DRUGS)}
    for row in sorted(rows, key=lambda r: order[r["query_name"]]):
        if row["ndc"] in by_ndc:
            print(f"DUPE ndc {row['ndc']}: {row['query_name']!r} dropped", file=sys.stderr)
            failures += 1
            continue
        by_ndc[row["ndc"]] = row

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(next(iter(by_ndc.values())).keys()))
        writer.writeheader()
        writer.writerows(by_ndc.values())
    print(f"wrote {len(by_ndc)} drugs to {OUT} ({failures} failures)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
