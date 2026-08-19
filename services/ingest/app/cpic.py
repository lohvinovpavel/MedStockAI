"""Tier 3 feed: CPIC gene–drug recommendations into `pgx_guideline`.

docs/patient-profiling-usecases.md §3 Tier 3 and §4. CPIC is the Tier 3
backbone: level A/B gene–drug pairs with dosing actions, free, keyless, and —
the part that makes it cheap here — **already RxNorm-coded**. `drugid` arrives
as "RxNorm:38400", so guidelines join straight onto the formulary with no
mapping layer to build or audit.

No model is involved. This is a download and a join: CPIC states the
recommendation, we store it verbatim, and stage 8 reads it back. That is why
Tier 3 is described as trivially explainable while Tier 2 is not.

Two things the API taught us that the design did not expect:

* **Match on `phenotypes`, not `lookupkey`.** They differ on 673 of 1000 rows:
  `lookupkey` is CPIC's machine key, which for CYP2D6 is an activity score
  ("0.25"), while `phenotypes` is the clinical phenotype ("Intermediate
  Metabolizer") — the thing a lab actually reports. Several activity scores
  collapse onto one phenotype, which the natural key here deduplicates.
* **`dosinginformation`, `alternatedrugavailable` and `otherprescribingguidance`
  are `false` on every row.** They look like exactly the structured
  actionable flag this needs, and they are unpopulated across the whole
  recommendation set. So `action_required` is derived from the phenotype
  instead, via `is_baseline_phenotype` — a short enumerated list that lives in
  `patient.py` and is ours rather than CPIC's. That is documented there because
  it is the one piece of clinical judgement in this feed.

`classification` — CPIC's own strength (Strong / Moderate / Optional) — becomes
the finding's weight where a weight applies.

Run:  uv run python -m app.cpic            (whole level A/B set)
      uv run python -m app.cpic --formulary  (only drugs we stock)
"""

from __future__ import annotations

import argparse
import sys

from medstock_shared.db import SessionLocal
from medstock_shared.models import FormularyItem, Hospital, PgxGuideline
from medstock_shared.patient import is_baseline_phenotype
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from ._source import fetch_json

API = "https://api.cpicpgx.org/v1"
GUIDELINE_URL = "https://cpicpgx.org/guidelines/"

# CPIC ships recommendations for populations beyond the general one (paediatric,
# and a few disease-specific splits). The vector carries an age band, not a
# population, so matching a paediatric row to an adult would be inventing a fit.
# Only "general" is loaded; the rest are a deliberate gap, not an oversight.
POPULATION = "general"


def _rxcui(drugid: str | None) -> str | None:
    """CPIC's drugid is "RxNorm:38400" for the drugs we can use, and a
    non-RxNorm identifier for the rest. Those are skipped rather than
    force-fitted -- a guideline joined to the wrong drug is worse than a
    guideline we do not hold."""
    if not drugid or not drugid.startswith("RxNorm:"):
        return None
    return drugid.split(":", 1)[1].strip() or None


def pair_levels() -> dict[str, str]:
    """(gene, rxcui) -> CPIC level, for the A/B pairs only."""
    rows = fetch_json(f"{API}/pair", {"select": "genesymbol,drugid,cpiclevel"})
    levels: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        rxcui = _rxcui(row.get("drugid"))
        level = str(row.get("cpiclevel") or "").strip()
        if rxcui and level in ("A", "B"):
            levels[f"{row.get('genesymbol')}|{rxcui}"] = level
    return levels


def _gene_phenotypes(phenotypes) -> list[tuple[str, str]]:
    """CPIC's `phenotypes` is {gene: phenotype}.

    A row keyed on two genes at once ("CYP2D6 IM *and* CYP2C19 IM") is a
    combination we cannot evaluate from a flat phenotype list without asserting
    the patient matches both, so it is skipped rather than half-matched on one
    of them. Those are a real gap in coverage, not a rounding error.
    """
    if not isinstance(phenotypes, dict) or len(phenotypes) != 1:
        return []
    return [(str(g), str(p)) for g, p in phenotypes.items() if g and p]


def recommendations(levels: dict[str, str]) -> list[dict]:
    rows = fetch_json(
        f"{API}/recommendation",
        {
            "select": "drugid,implications,drugrecommendation,classification,phenotypes,population",
        },
    )
    out: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        rxcui = _rxcui(row.get("drugid"))
        if not rxcui or str(row.get("population") or "").strip() != POPULATION:
            continue
        for gene, phenotype in _gene_phenotypes(row.get("phenotypes")):
            level = levels.get(f"{gene}|{rxcui}")
            if level is None:
                continue  # not a level A/B pair; out of Tier 3 scope
            implications = row.get("implications")
            implication = ""
            if isinstance(implications, dict):
                implication = str(implications.get(gene) or "")
            out.append(
                {
                    "gene": gene,
                    "rxcui": rxcui,
                    "phenotype": phenotype,
                    "recommendation": str(row.get("drugrecommendation") or "")[:2000],
                    "implication": implication[:2000],
                    "classification": str(row.get("classification") or "")[:40],
                    "evidence_level": level,
                    "action_required": not is_baseline_phenotype(phenotype),
                    "population": POPULATION,
                    "source_url": GUIDELINE_URL,
                }
            )
    return out


def formulary_rxcuis() -> set[str]:
    with SessionLocal() as session:
        hospital_ids = session.scalars(select(Hospital.id)).all()
        all_rxcuis: set[str] = set()
        for hid in hospital_ids:
            session.execute(
                text("SELECT set_config('app.hospital_id', :h, true)"),
                {"h": str(hid)},
            )
            for r in session.scalars(
                select(FormularyItem.rxcui).where(FormularyItem.rxcui.is_not(None))
            ):
                all_rxcuis.add(str(r))
        return all_rxcuis


def write(rows: list[dict]) -> int:
    """Upsert on the natural key. Unlike the PP-3 profiles there is no approval
    to reset: CPIC is a published guideline, not a model's reading of one, so
    re-running this refreshes the text and nothing else."""
    if not rows:
        return 0
    with SessionLocal() as session:
        for row in rows:
            session.execute(
                insert(PgxGuideline)
                .values(**row)
                .on_conflict_do_update(
                    index_elements=["gene", "rxcui", "phenotype", "population"],
                    set_={
                        k: v
                        for k, v in row.items()
                        if k not in ("gene", "rxcui", "phenotype", "population")
                    },
                )
            )
        session.commit()
    return len(rows)


def run(only_formulary: bool) -> int:
    levels = pair_levels()
    print(f"  {len(levels)} level A/B gene-drug pairs with an RxCUI")
    rows = recommendations(levels)
    if only_formulary:
        stocked = formulary_rxcuis()
        before = len(rows)
        rows = [r for r in rows if r["rxcui"] in stocked]
        print(f"  {before} -> {len(rows)} recommendations after the formulary filter")
    written = write(rows)
    actionable = sum(1 for r in rows if r["action_required"])
    print(f"  {written} recommendation(s), {actionable} of them actionable")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Load CPIC Tier 3 guidelines")
    parser.add_argument(
        "--formulary", action="store_true", help="only drugs in the stocked formulary"
    )
    args = parser.parse_args()
    try:
        run(args.formulary)
    except Exception as exc:  # noqa: BLE001 — a feed failure is not a traceback for ops
        print(f"cpic: FAILED {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
