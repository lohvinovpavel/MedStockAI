"""COMP-1 demo data: certification rows for whatever is already on the shelf.

Runs after `scripts/seed_stock.py` and reads `stock_snapshot` rather than
inventing its own NDCs — a badge is only visible next to a stock row, so the two
seeds have to agree on the same products.

**The statuses are not hardcoded.** Each scenario supplies the same source facts
openFDA would (an expiry date, a recall) and the real rule engine in
`medstock_shared.certification` derives the colour. So this script cannot drift
from the rules it is demonstrating: change a threshold, re-run, and the demo
changes with it. It also means running it is a live check that the rules still
produce the scenarios the demo needs.

  ENVIRONMENT=demo PYTHONPATH=shared python scripts/seed_certification.py

Idempotent: certification rows upsert on `ndc`, findings are replaced per NDC.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# scripts/ → repo root on sys.path is not enough; shared lives in shared/.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "shared"))

from medstock_shared.certification import (
    RULESET_VERSION,
    Recall,
    Status,
    evaluate,
    status_for,
)
from medstock_shared.db import SessionLocal
from medstock_shared.models import (
    CertificationFinding,
    DrugCertification,
    StockSnapshot,
)
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

TODAY = datetime.now(tz=UTC).date()

# Every row this script writes is marked `demo`, never `scheduled` — so a real
# CronJob row and a fabricated one are never confused, in the database or in a
# Director export (docs/demo-data.md §5).
PROVENANCE = "demo"

# The scenarios the demo has to be able to show. Order is meaningful: they are
# dealt onto the shelf's NDCs in sorted order so the same NDC gets the same
# scenario on every run.
#
# `expect` is asserted after the rules run. If a threshold changes and a
# scenario stops producing its colour, this seed fails loudly rather than
# quietly handing the demo a screen with no red badge on it.
SCENARIOS: tuple[dict, ...] = (
    {
        "label": "listing expired 40 days ago",
        "expect": Status.RED,
        "facts": {"listing_expiration_date": TODAY - timedelta(days=40)},
        "extra": {"marketing_category": "ANDA"},
    },
    {
        "label": "Class I recall ongoing",
        "expect": Status.RED,
        "facts": {
            "marketing_end_date": TODAY + timedelta(days=900),
            "recalls": [
                Recall(
                    classification="Class I",
                    status="Ongoing",
                    recall_number="D-2026-0417",
                    reason="Sterility assurance failure identified during routine audit",
                )
            ],
        },
        "extra": {"marketing_category": "NDA"},
    },
    {
        "label": "marketing ends in 21 days",
        "expect": Status.YELLOW,
        "facts": {"marketing_end_date": TODAY + timedelta(days=21)},
        "extra": {"marketing_category": "ANDA"},
    },
    {
        "label": "Class II recall ongoing",
        "expect": Status.YELLOW,
        "facts": {
            "marketing_end_date": TODAY + timedelta(days=900),
            "recalls": [
                Recall(
                    classification="Class II",
                    status="Ongoing",
                    recall_number="D-2026-0512",
                    reason="Dissolution specification not met at 12-month stability point",
                )
            ],
        },
        "extra": {"marketing_category": "ANDA"},
    },
    {
        "label": "unapproved marketing category",
        "expect": Status.YELLOW,
        "facts": {
            "marketing_end_date": TODAY + timedelta(days=900),
            "marketing_category": "UNAPPROVED DRUG OTHER",
        },
        "extra": {"marketing_category": "UNAPPROVED DRUG OTHER"},
    },
    {
        "label": "no expiry dates in the source record",
        "expect": Status.GREEN,  # green, but carrying DATES_UNKNOWN
        "facts": {},
        "extra": {"marketing_category": "OTC MONOGRAPH DRUG"},
    },
    {
        "label": "terminated recall — history, not a live signal",
        "expect": Status.GREEN,
        "facts": {
            "marketing_end_date": TODAY + timedelta(days=900),
            "recalls": [
                Recall(
                    classification="Class I",
                    status="Terminated",
                    recall_number="D-2024-0090",
                    reason="Resolved 2024",
                )
            ],
        },
        "extra": {"marketing_category": "NDA"},
    },
)

def shelf_ndcs(session: Session, hospital_id: str | None) -> list[str]:
    stmt = select(StockSnapshot.ndc).distinct()
    if hospital_id:
        stmt = stmt.where(StockSnapshot.hospital_id == hospital_id)
    return sorted({str(n) for n in session.scalars(stmt).all()})


def certified_ndcs(session: Session) -> set[str]:
    """NDCs the *real* feed has answered for.

    Demo rows are excluded on purpose: a previous run of this script must not
    make its own NDCs look taken, or each run would pick a fresh set and the
    demo would drift. Ignoring them means a re-run lands on the same NDCs and
    simply overwrites itself — idempotent, like every other seed here.
    """
    return {
        str(n)
        for n in session.scalars(
            select(DrugCertification.ndc).where(DrugCertification.provenance != PROVENANCE)
        ).all()
    }


def plan(shelf: list[str], already_certified: set[str]) -> list[tuple[str, dict]]:
    """Deal each scenario onto one NDC the real feed could not certify.

    Two rules, both deliberate:

    * **Never overwrite a real row.** A drug openFDA actually answered for keeps
      its real status — the demo is not allowed to repaint live data.
    * **Only as many rows as there are scenarios.** Everything else is left
      alone: real where the feed reached it, Unknown where it did not. A demo
      where every badge is known never exercises the grey state COMP-2 exists
      to resolve.
    """
    free = [n for n in shelf if n not in already_certified]
    return list(zip(free, SCENARIOS))


def rows_for(ndc: str, scenario: dict) -> tuple[dict, list[dict], Status]:
    findings = evaluate(today=TODAY, **scenario["facts"])
    status = status_for(findings)
    facts = scenario["facts"]
    certification = {
        "ndc": ndc,
        "status": str(status),
        "marketing_end_date": facts.get("marketing_end_date"),
        "listing_expiration_date": facts.get("listing_expiration_date"),
        "marketing_category": scenario["extra"].get("marketing_category"),
        "application_number": None,
        "labeler": "DEMO GENERAL PHARMACEUTICALS",
        "provenance": PROVENANCE,
        "ruleset_version": RULESET_VERSION,
        "raw": {"demo_scenario": scenario["label"]},
    }
    finding_rows = [
        {
            "ndc": ndc,
            "code": f.code,
            "severity": str(f.severity),
            "message": f.message,
            "source": f.source,
            "source_url": f.source_url,
            "source_ref": f.source_ref,
            "raw": {},
        }
        for f in findings
    ]
    return certification, finding_rows, status


def main() -> int:
    if os.environ.get("ENVIRONMENT") != "demo":
        print("refusing to run: set ENVIRONMENT=demo", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(description="Seed COMP-1 certification demo rows")
    parser.add_argument("--hospital-id", default=None, help="limit to one hospital's shelf")
    args = parser.parse_args()

    with SessionLocal() as session:
        ndcs = shelf_ndcs(session, args.hospital_id)
        if not ndcs:
            print("no stock_snapshot rows — run scripts/seed_stock.py first", file=sys.stderr)
            return 1
        real = certified_ndcs(session)
        assignments = plan(ndcs, real)
        if len(assignments) < len(SCENARIOS):
            print(
                f"only {len(assignments)} uncertified NDC(s) available for "
                f"{len(SCENARIOS)} scenarios — seed more stock first",
                file=sys.stderr,
            )
            return 1

        written = 0
        for ndc, scenario in assignments:
            certification, findings, status = rows_for(ndc, scenario)
            if status is not scenario["expect"]:
                print(
                    f"rules no longer produce {scenario['expect']} for "
                    f"'{scenario['label']}' (got {status}) — demo scenario is stale",
                    file=sys.stderr,
                )
                return 1
            session.execute(
                insert(DrugCertification)
                .values(**certification)
                .on_conflict_do_update(
                    index_elements=["ndc"],
                    set_={k: v for k, v in certification.items() if k != "ndc"},
                )
            )
            session.execute(delete(CertificationFinding).where(CertificationFinding.ndc == ndc))
            if findings:
                session.execute(insert(CertificationFinding).values(findings))
            written += 1
            print(f"  {status.value:7} {ndc:16} {scenario['label']}")
        session.commit()

    # Counted against this shelf only — `real` spans every NDC openFDA answered
    # for, most of which this hospital does not stock.
    real_on_shelf = len(set(ndcs) & real)
    print(
        f"\n{written} demo row(s) · {real_on_shelf} real openFDA row(s) untouched · "
        f"{len(ndcs) - real_on_shelf - written} NDC(s) left Unknown"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
