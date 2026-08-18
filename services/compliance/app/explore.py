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
can set red (docs/compliance-usecases.md §4.3). Import alerts are FDA, also
formal, but raise **yellow** — detention without physical examination is a
standing posture on a manufacturer, not a defect found in this product. News
raises yellow and can never raise red, whatever it says.

Every row written is `provenance='on_demand'` and carries `expires_at`. Nothing
refreshes it on a schedule, so it has to expire itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from medstock_shared.certification import (
    RULESET_VERSION,
    AlertListing,
    NewsItem,
    WarningAction,
    evaluate,
    firm_key,
    ndc11,
    parse_fda_date,
    product_ndc_candidates,
    status_for,
)
from medstock_shared.models import (
    CertificationFinding,
    DrugCertification,
    ImportAlert,
    NewsSignal,
    WarningLetter,
)
from medstock_shared.ndc_status import fetch_ndc_status
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

NDC_URL = "https://api.fda.gov/drug/ndc.json"
_TIMEOUT = 15.0

# A badge is a summary, not a feed reader. Beyond a handful the list stops being
# something a pharmacist scans and starts being something they scroll past.
MAX_NEWS = 5

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


def import_alerts_for(session: Session, labeler: str | None) -> list[AlertListing]:
    """Red List entries whose firm name matches this NDC's labeler.

    **Exact match on the normalised key, never fuzzy** — `firm_key` carries the
    argument. Briefly: a miss looks like every other drug that is not on an
    alert, while a false positive publicly accuses a named manufacturer of being
    detained at the border. Those are not symmetric errors.

    A missing table degrades to "no listings" rather than failing the check: the
    formal openFDA findings are the backbone of a badge and must still be
    computable when the weekly scrape has never run.
    """
    if not labeler:
        return []
    try:
        rows = session.scalars(
            select(ImportAlert).where(ImportAlert.firm_key == firm_key(labeler))
        ).all()
    except (ProgrammingError, SQLAlchemyError):
        return []
    return [
        AlertListing(
            alert_number=row.alert_number,
            firm_name=row.firm_name,
            country=row.country or "",
            listed_at=row.listed_at,
            source_url=row.source_url or "",
        )
        for row in rows
    ]


def warning_letters_for(session: Session, labeler: str | None) -> list[WarningAction]:
    """Warning letters naming this labeler, matched exactly like import alerts.

    Recency is applied in `evaluate` rather than here so the window lives beside
    the rule it belongs to and one table read serves both.
    """
    if not labeler:
        return []
    try:
        rows = session.scalars(
            select(WarningLetter).where(WarningLetter.firm_key == firm_key(labeler))
        ).all()
    except (ProgrammingError, SQLAlchemyError):
        return []
    return [
        WarningAction(
            company_name=row.company_name,
            issue_date=row.issue_date,
            issuing_office=row.issuing_office or "",
            subject=row.subject or "",
            source_url=row.source_url or "",
        )
        for row in rows
    ]


def news_for(session: Session, ndc: str) -> list[NewsItem]:
    """Recent press mentions attached to this NDC. Yellow at most — §4.3."""
    try:
        rows = session.scalars(
            select(NewsSignal)
            .where(NewsSignal.ndc == ndc)
            .order_by(NewsSignal.published_at.desc().nullslast())
            .limit(MAX_NEWS)
        ).all()
    except (ProgrammingError, SQLAlchemyError):
        return []
    return [
        NewsItem(
            headline=row.headline,
            url=row.url,
            domain=row.domain or "",
            published_at=row.published_at,
        )
        for row in rows
    ]


def explore(session: Session, ndc: str) -> dict:
    """Resolve one NDC and persist the verdict. Returns the stored row shape.

    Safe to call twice: the certification row upserts on `ndc` and its findings
    are replaced, exactly as the scheduled feed does.
    """
    key = ndc11(str(ndc))
    product = _directory_record(key)
    status_record = fetch_ndc_status(key)

    labeler = (product or {}).get("labeler_name")
    findings = evaluate(
        marketing_end_date=parse_fda_date((product or {}).get("marketing_end_date")),
        marketing_start_date=parse_fda_date((product or {}).get("marketing_start_date")),
        listing_expiration_date=parse_fda_date((product or {}).get("listing_expiration_date")),
        marketing_category=(product or {}).get("marketing_category"),
        finished=(product or {}).get("finished"),
        import_alerts=import_alerts_for(session, labeler),
        warning_letters=warning_letters_for(session, labeler),
        news=news_for(session, key),
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
