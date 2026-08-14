"""COMP-2 — on-demand exploration of a drug nobody has asked about before.

The scheduled feed (`services/ingest/app/certification.py`) certifies what is on
a shelf. Analogue search surfaces drugs that are not, and those come back
`unknown`. This resolves them at the moment someone looks.

**Why a second source rather than re-querying the first.** The openFDA NDC
Directory holds currently-marketed products only, so asking it again about a
drug it has already dropped answers nothing. Measured against 18 real unknowns:
the directory resolved 0, the SPL label endpoint 3, RxNorm's NDC status endpoint
all 18 — including one obsolete since 2012, which is a red badge the scheduled
feed could never have produced.

**Rules for what this may conclude.** RxNorm is NLM, a government source, so it
can set red (docs/compliance-usecases.md §4.3). News is not consulted here; when
it is, it will only ever raise yellow.

Every row written is `provenance='on_demand'` and carries `expires_at`. Nothing
refreshes it on a schedule, so it has to expire itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from medstock_shared.certification import (
    RULESET_VERSION,
    evaluate,
    ndc11,
    parse_fda_date,
    product_ndc_candidates,
    status_for,
)
from medstock_shared.models import CertificationFinding, DrugCertification
from medstock_shared.ndc_status import fetch_ndc_status
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

NDC_URL = "https://api.fda.gov/drug/ndc.json"
_TIMEOUT = 15.0

# An on-demand row has no CronJob behind it. A week is long enough that a demo
# or a busy afternoon does not re-spend the openFDA budget on the same drug, and
# short enough that a recall appearing next week is not missed indefinitely.
TTL_DAYS = 7


def _directory_record(ndc: str) -> dict | None:
    """The product record for one NDC, if the directory has it after all.

    Cheap and worth trying: the scheduled sweep is bounded, so an NDC can be
    absent from our table and present upstream.
    """
    terms = " OR ".join(f'"{c}"' for c in product_ndc_candidates(ndc))
    try:
        response = httpx.get(
            NDC_URL, params={"search": f"product_ndc:({terms})", "limit": 1}, timeout=_TIMEOUT
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    results = response.json().get("results") or []
    return results[0] if results else None


def explore(session: Session, ndc: str) -> dict:
    """Resolve one NDC and persist the verdict. Returns the stored row shape.

    Safe to call twice: the certification row upserts on `ndc` and its findings
    are replaced, exactly as the scheduled feed does.
    """
    key = ndc11(str(ndc))
    product = _directory_record(key)
    status_record = fetch_ndc_status(key)

    findings = evaluate(
        marketing_end_date=parse_fda_date((product or {}).get("marketing_end_date")),
        marketing_start_date=parse_fda_date((product or {}).get("marketing_start_date")),
        listing_expiration_date=parse_fda_date((product or {}).get("listing_expiration_date")),
        marketing_category=(product or {}).get("marketing_category"),
        finished=(product or {}).get("finished"),
        ndc_status=status_record,
        in_directory=product is not None,
    )

    now = datetime.now(tz=UTC)
    certification = {
        "ndc": key,
        "status": str(status_for(findings)),
        "marketing_end_date": parse_fda_date((product or {}).get("marketing_end_date")),
        "listing_expiration_date": parse_fda_date((product or {}).get("listing_expiration_date")),
        "marketing_category": (product or {}).get("marketing_category"),
        "application_number": (product or {}).get("application_number"),
        "labeler": (product or {}).get("labeler_name"),
        "provenance": "on_demand",
        "ruleset_version": RULESET_VERSION,
        "expires_at": now + timedelta(days=TTL_DAYS),
        "raw": {
            "directory": product or {},
            "rxnorm": getattr(status_record, "raw", {}) if status_record else {},
        },
    }

    session.execute(
        insert(DrugCertification)
        .values(**certification)
        .on_conflict_do_update(
            index_elements=["ndc"],
            set_={k: v for k, v in certification.items() if k != "ndc"},
        )
    )
    session.execute(delete(CertificationFinding).where(CertificationFinding.ndc == key))
    if findings:
        session.execute(
            insert(CertificationFinding).values(
                [
                    {
                        "ndc": key,
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
            )
        )
    session.commit()

    return {
        "ndc": key,
        "status": certification["status"],
        "provenance": "on_demand",
        "expires_at": certification["expires_at"].isoformat(),
        "sources_consulted": {
            "openfda_ndc_directory": product is not None,
            "rxnorm_ndc_status": status_record is not None,
        },
        "codes": [f.code for f in findings],
    }


def is_stale(record: DrugCertification, now: datetime | None = None) -> bool:
    """On-demand rows expire; scheduled ones are refreshed by their CronJob and
    never go stale on their own."""
    if record.expires_at is None:
        return False
    return record.expires_at <= (now or datetime.now(tz=UTC))
