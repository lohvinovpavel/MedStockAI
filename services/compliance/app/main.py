"""compliance — certification traffic light (COMP-1).

Reads reference tables only. `drug_certification` has no `hospital_id` and no
RLS (services.md §1.1), so these handlers use a plain session rather than
`session_scope` — there is no tenant context to set. Authentication is still
required: the colour is not secret, but the endpoint is not public either.
"""

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from medstock_shared.auth import Principal, require
from medstock_shared.certification import (
    RULESET_VERSION,
    Finding,
    Status,
    ruleset,
    signal,
)
from medstock_shared.db import engine
from medstock_shared.models import CertificationFinding, DrugCertification
from sqlalchemy import case, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.explore import TTL_DAYS, explore, is_stale

app = FastAPI(title="compliance")

# One page of a stock list, not a bulk export. The Director CSV export is a
# separate endpoint and is not implemented in this pass.
MAX_BATCH = 100

# Exploration is two upstream calls per NDC against a shared daily budget, so it
# is capped far lower than a lookup.
MAX_EXPLORE = 10

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


@app.get("/version")
def version() -> dict[str, str]:
    """GIT_SHA is baked in at image build time (Dockerfile) — unset outside
    a built container, e.g. running locally from source. semver comes from
    the installed medstock-compliance package (pyproject.toml), not the image."""
    try:
        semver = pkg_version("medstock-compliance")
    except PackageNotFoundError:
        semver = "unknown"
    return {
        "service": "compliance",
        "version": os.environ.get("GIT_SHA", "unknown"),
        "semver": semver,
    }


@app.get("/ruleset")
def get_ruleset(_: Principal = Depends(require("inventory:read"))) -> dict:
    """Every rule that can produce a colour, and the thresholds behind them."""
    return ruleset()


@app.get("/status")
def get_status(
    ndc: list[str] = Query(default=[], description="Repeatable. One page of stock, max 100."),
    _: Principal = Depends(require("certificate:read")),
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
        codes = _codes_by_ndc(session, list(known))

    results = []
    for value in wanted:
        if value not in known:
            results.append(
                {
                    "ndc": value,
                    "status": str(Status.UNKNOWN),
                    "attention": str(Status.UNKNOWN),
                    "reasons": 0,
                    "transient": 0,
                    "persistent": 0,
                    "categories": {},
                    "codes": [],
                }
            )
            continue
        detail = signal([Finding(code=c, message="", source="") for c in codes.get(value, [])])
        # The stored colour wins over the recomputed one: it is what the feed
        # decided, and a ruleset change must not silently repaint history
        # without a re-ingest.
        detail["status"] = known[value]
        results.append({"ndc": value, **detail})

    return {"ruleset_version": RULESET_VERSION, "results": results}


def _codes_by_ndc(session: Session, ndcs: list[str]) -> dict[str, list[str]]:
    """Finding codes per NDC. Severity, category and transience are all derived
    from the code, so this is everything the badge detail needs — no need to
    haul message text back for a page of stock."""
    if not ndcs:
        return {}
    # Through the same guard as everything else: one of the two tables existing
    # without the other is still "not migrated", not a 500.
    rows = _rows_or_503(
        session,
        select(CertificationFinding.ndc, CertificationFinding.code).where(
            CertificationFinding.ndc.in_(ndcs)
        ),
    )
    out: dict[str, list[str]] = {}
    for ndc_value, code in rows:
        out.setdefault(str(ndc_value), []).append(str(code))
    return out


@app.post("/explore")
def post_explore(
    payload: dict = Body(default={}),
    _: Principal = Depends(require("certification:explore")),
) -> dict:
    """COMP-2, explicitly, for a handful of NDCs at once.

    Kept off `GET /status` on purpose: that endpoint serves a whole page of
    stock from one indexed read, and turning it into N upstream calls would
    make a stock list as slow as the slowest third party — and spend the
    openFDA daily budget on drugs nobody asked about.
    """
    wanted = list(dict.fromkeys(str(n) for n in (payload.get("ndc") or []) if n))
    if not wanted:
        raise HTTPException(status_code=422, detail="ndc must not be empty")
    if len(wanted) > MAX_EXPLORE:
        raise HTTPException(status_code=400, detail=f"at most {MAX_EXPLORE} ndc values per call")

    results, errors = [], {}
    with Session(engine) as session:
        for value in wanted:
            try:
                results.append(explore(session, value))
            except Exception as exc:  # noqa: BLE001 — one bad upstream must not
                session.rollback()  # lose the answers we did get
                errors[value] = str(exc)[:200]
    return {"ruleset_version": RULESET_VERSION, "ttl_days": TTL_DAYS,
            "results": results, "errors": errors}


@app.get("/certificates/{ndc}")
def get_certificate(
    ndc: str,
    _: Principal = Depends(require("certificate:read")),
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

        # COMP-2: a miss here is the moment someone is actually looking at this
        # drug, so it is the right moment to go and find out. A stale on-demand
        # row is re-explored for the same reason.
        if record is None or is_stale(record):
            try:
                explore(session, ndc)
            except Exception as exc:  # noqa: BLE001 — upstreams are not ours
                session.rollback()
                if record is None:
                    # Uniform shape so the UI still renders a grey badge.
                    return {
                        "ndc": ndc,
                        "status": str(Status.UNKNOWN),
                        "ruleset_version": RULESET_VERSION,
                        "explored": False,
                        "explore_error": str(exc)[:200],
                        "findings": [],
                    }
            record = session.execute(
                select(DrugCertification).where(DrugCertification.ndc == ndc)
            ).scalar_one_or_none()

        if record is None:
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
        "signal": signal([Finding(code=f.code, message="", source="") for f in findings]),
        "findings": [
            {
                "code": f.code,
                "severity": f.severity,
                "category": str(Finding(code=f.code, message="", source="").category),
                "transient": Finding(code=f.code, message="", source="").transient,
                "message": f.message,
                "source": f.source,
                "source_url": f.source_url,
                "source_ref": f.source_ref,
                "observed_at": f.observed_at.isoformat() if f.observed_at else None,
            }
            for f in findings
        ],
    }
