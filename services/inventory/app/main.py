import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from medstock_shared.auth import Principal, require
from medstock_shared.db import engine, session_scope
from medstock_shared.models import StockSnapshot
from medstock_shared.rxnorm import RxNormError, ndcs_for_rxcui
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

app = FastAPI(title="inventory")
stock = APIRouter()


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
    the installed medstock-inventory package (pyproject.toml), not the image."""
    try:
        semver = pkg_version("medstock-inventory")
    except PackageNotFoundError:
        semver = "unknown"
    return {"service": "inventory", "version": os.environ.get("GIT_SHA", "unknown"), "semver": semver}


@stock.get("/stock")
def get_stock(
    rxcui: str = Query(..., min_length=1, max_length=32),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    rxcui = rxcui.strip()
    try:
        ndcs = ndcs_for_rxcui(rxcui)
    except RxNormError as exc:
        raise HTTPException(status_code=503, detail="rxnorm unavailable") from exc

    items: list[dict] = []
    if ndcs:
        try:
            with session_scope(principal.hospital_id, principal.user_id) as session:
                rows = session.scalars(
                    select(StockSnapshot).where(StockSnapshot.ndc.in_(ndcs))
                ).all()
                items = [
                    {
                        "ndc": row.ndc,
                        "quantity": row.quantity,
                        "location_id": row.location_id or None,
                    }
                    for row in rows
                ]
        except SQLAlchemyError:
            items = []

    return {"rxcui": rxcui, "ndc_count": len(ndcs), "items": items}


app.include_router(stock)
app.include_router(stock, prefix="/api/inventory")
