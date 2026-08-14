"""compliance — certification traffic light (COMP-1).

Reads reference tables only. `drug_certification` has no `hospital_id` and no
RLS (services.md §1.1), so these handlers use a plain session rather than
`session_scope` — there is no tenant context to set. Authentication is still
required: the colour is not secret, but the endpoint is not public either.
"""

from fastapi import Depends, FastAPI, HTTPException, Query
from medstock_shared.auth import Principal, require
from medstock_shared.certification import RULESET_VERSION, Status, ruleset
from medstock_shared.db import engine
from medstock_shared.models import CertificationFinding, DrugCertification
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

app = FastAPI(title="compliance")

# One page of a stock list, not a bulk export. The Director CSV export is a
# separate endpoint and is not implemented in this pass.
MAX_BATCH = 100

_NOT_MIGRATED = "certification tables are not migrated"

# Severity is stored as text, and ordering by it directly is alphabetical —
# "info" then "red" then "yellow", which puts a Class I recall below a note
# saying we had no dates. Rank it explicitly: worst reason first.
_SEVERITY_RANK = case(
    (CertificationFinding.severity == "red", 0),
    (CertificationFinding.severity == "yellow", 1),
    else_=2,
)


def _rows_or_503(session: Session, stmt):
    """A missing table is a deployment fault, not an empty result. Returning
    green here would be the system reporting a clean bill from a check that
    never ran — see docs/compliance-usecases.md §3.1."""
    try:
        return session.execute(stmt).all()
    except ProgrammingError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=_NOT_MIGRATED) from exc


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependencies checked on purpose —
    a database blip must not get every pod restarted."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/ruleset")
def get_ruleset(_: Principal = Depends(require("inventory:read"))) -> dict:
    """Every rule that can produce a colour, and the thresholds behind them."""
    return ruleset()


@app.get("/status")
def get_status(
    ndc: list[str] = Query(default=[], description="Repeatable. One page of stock, max 100."),
    _: Principal = Depends(require("inventory:read")),
) -> dict:
    """Batch traffic light. Built for the inventory page: one call per page of
    stock rather than one call per row.

    An NDC we hold no record for comes back `unknown`, not `green` — that is the
    state COMP-2's on-demand exploration is meant to resolve.
    """
    wanted = list(dict.fromkeys(n for n in ndc if n))
    if not wanted:
        return {"ruleset_version": RULESET_VERSION, "results": []}
    if len(wanted) > MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"at most {MAX_BATCH} ndc values per call")

    with Session(engine) as session:
        rows = _rows_or_503(
            session,
            select(DrugCertification.ndc, DrugCertification.status).where(
                DrugCertification.ndc.in_(wanted)
            ),
        )
        known = {str(row_ndc): str(row_status) for row_ndc, row_status in rows}
        counts = _finding_counts(session, list(known))

    return {
        "ruleset_version": RULESET_VERSION,
        "results": [
            {
                "ndc": value,
                "status": known.get(value, str(Status.UNKNOWN)),
                "reasons": counts.get(value, 0),
            }
            for value in wanted
        ],
    }


def _finding_counts(session: Session, ndcs: list[str]) -> dict[str, int]:
    """How many reasons sit behind each colour, so the UI can badge
    '2 reasons' without fetching the evidence for every row."""
    if not ndcs:
        return {}
    # Through the same guard as everything else: one of the two tables existing
    # without the other is still "not migrated", not a 500.
    rows = _rows_or_503(
        session,
        select(CertificationFinding.ndc, func.count())
        .where(CertificationFinding.ndc.in_(ndcs))
        .group_by(CertificationFinding.ndc),
    )
    return {str(n): int(c) for n, c in rows}


@app.get("/certificates/{ndc}")
def get_certificate(
    ndc: str,
    _: Principal = Depends(require("inventory:read")),
) -> dict:
    """The evidence behind one colour: every finding, with the source that
    produced it. This is what a pharmacist opens when they disagree."""
    with Session(engine) as session:
        try:
            record = session.execute(
                select(DrugCertification).where(DrugCertification.ndc == ndc)
            ).scalar_one_or_none()
        except ProgrammingError as exc:
            session.rollback()
            raise HTTPException(status_code=503, detail=_NOT_MIGRATED) from exc

        if record is None:
            # Not a 404: unknown is a real state in the traffic light, and the
            # caller needs a uniform shape to render a grey badge.
            return {
                "ndc": ndc,
                "status": str(Status.UNKNOWN),
                "ruleset_version": RULESET_VERSION,
                "explored": False,
                "findings": [],
            }

        findings = session.execute(
            select(CertificationFinding)
            .where(CertificationFinding.ndc == ndc)
            .order_by(_SEVERITY_RANK, CertificationFinding.code)
        ).scalars().all()

    return {
        "ndc": record.ndc,
        "status": record.status,
        "ruleset_version": record.ruleset_version,
        "provenance": record.provenance,
        "explored": True,
        "computed_at": record.computed_at.isoformat() if record.computed_at else None,
        "marketing_end_date": (
            record.marketing_end_date.isoformat() if record.marketing_end_date else None
        ),
        "listing_expiration_date": (
            record.listing_expiration_date.isoformat() if record.listing_expiration_date else None
        ),
        "marketing_category": record.marketing_category,
        "labeler": record.labeler,
        "findings": [
            {
                "code": f.code,
                "severity": f.severity,
                "message": f.message,
                "source": f.source,
                "source_url": f.source_url,
                "source_ref": f.source_ref,
                "observed_at": f.observed_at.isoformat() if f.observed_at else None,
            }
            for f in findings
        ],
    }
