import os
import uuid
from datetime import date
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from medstock_shared.auth import Principal, require
from medstock_shared.db import engine, session_scope
from medstock_shared.models import (
    Drug,
    Facility,
    FormularyItem,
    ParLevel,
    StockBatch,
    StockSnapshot,
)
from medstock_shared.rxnorm import RxNormError, ndcs_for_rxcui
from medstock_shared.stock import STATUS_RANK, derive_status, suggested_order_qty
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

app = FastAPI(title="inventory")
api = APIRouter()


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


class ReceiveBatchBody(BaseModel):
    facility_id: int
    ndc: str = Field(min_length=1, max_length=32)
    lot: str = Field(min_length=1, max_length=64)
    expiry_date: date
    quantity: int = Field(gt=0)
    location_id: str = ""


class ConsumeBody(BaseModel):
    quantity: int = Field(gt=0)
    reason: str = "dispense"


class ParBody(BaseModel):
    facility_id: int
    ndc: str = Field(min_length=1, max_length=32)
    reorder_point: int = Field(ge=0)
    target_qty: int = Field(gt=0)


def _facility(session, facility_id: int) -> Facility:
    row = session.get(Facility, facility_id)
    if row is None:
        raise HTTPException(status_code=404, detail="facility not found")
    return row


def _batch_dict(row: StockBatch, snapshot_qty: int | None = None) -> dict:
    return {
        "id": row.id,
        "facility_id": row.facility_id,
        "ndc": row.ndc,
        "lot": row.lot,
        "expiry_date": row.expiry_date.isoformat(),
        "quantity": row.quantity,
        "location_id": row.location_id or None,
        "received_at": row.received_at.isoformat() if row.received_at else None,
        "snapshot_quantity": snapshot_qty,
    }


@api.get("/stock")
def get_stock(
    rxcui: str = Query(..., min_length=1, max_length=32),
    facility_id: int | None = Query(None),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    rxcui = rxcui.strip()
    degraded = False
    try:
        ndcs = ndcs_for_rxcui(rxcui)
    except RxNormError:
        # A stock read must not 500 because NLM is down — match the string
        # against shelf NDCs and flag the degradation (B2 rule 3).
        ndcs = [rxcui]
        degraded = True

    items: list[dict] = []
    in_formulary = False
    try:
        with session_scope(principal.hospital_id, principal.user_id) as session:
            if facility_id is not None:
                _facility(session, facility_id)
            if ndcs:
                stmt = select(StockSnapshot).where(StockSnapshot.ndc.in_(ndcs))
                if facility_id is not None:
                    stmt = stmt.where(StockSnapshot.facility_id == facility_id)
                rows = session.scalars(stmt).all()
                items = [
                    {
                        "ndc": row.ndc,
                        "quantity": row.quantity,
                        "location_id": row.location_id or None,
                        "facility_id": row.facility_id,
                        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    }
                    for row in rows
                ]
            in_formulary = (
                session.scalar(
                    select(FormularyItem.id).where(FormularyItem.rxcui == rxcui).limit(1)
                )
                is not None
            )
    except HTTPException:
        raise
    except SQLAlchemyError:
        items = []

    return {
        "rxcui": rxcui,
        "facility_id": facility_id,
        "ndc_count": len(ndcs),
        "rxnorm_degraded": degraded,
        "in_formulary": in_formulary,
        "items": items,
    }


@api.get("/items")
def list_items(
    facility_id: int | None = Query(None),
    q: str | None = Query(None),
    status: str | None = Query(None),
    expiring_before: date | None = Query(None),
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    if limit > 200:
        raise HTTPException(status_code=422, detail="limit must be <= 200")
    if status is not None and status not in STATUS_RANK:
        raise HTTPException(status_code=422, detail="unknown status")

    with session_scope(principal.hospital_id, principal.user_id) as session:
        if facility_id is not None:
            _facility(session, facility_id)

        # Aggregate batches first. Joining lots onto snapshot rows and then
        # SUM(quantity) would multiply on-hand by the lot count.
        batch_stats = (
            select(
                StockBatch.facility_id,
                StockBatch.ndc,
                func.min(StockBatch.expiry_date).label("earliest_expiry"),
                func.array_agg(
                    aggregate_order_by(
                        StockBatch.lot,
                        StockBatch.expiry_date.asc(),
                        StockBatch.id.asc(),
                    )
                ).label("lots"),
            )
            .where(StockBatch.quantity > 0)
            .group_by(StockBatch.facility_id, StockBatch.ndc)
            .subquery()
        )

        stmt = (
            select(
                StockSnapshot.facility_id,
                StockSnapshot.ndc,
                func.coalesce(func.sum(StockSnapshot.quantity), 0).label("quantity"),
                func.min(StockSnapshot.location_id).label("location_id"),
                batch_stats.c.earliest_expiry,
                batch_stats.c.lots,
                Drug.name.label("name"),
                ParLevel.reorder_point,
                ParLevel.target_qty,
            )
            .outerjoin(
                Drug,
                Drug.ndc == StockSnapshot.ndc,
            )
            .outerjoin(
                ParLevel,
                (ParLevel.facility_id == StockSnapshot.facility_id)
                & (ParLevel.ndc == StockSnapshot.ndc),
            )
            .outerjoin(
                batch_stats,
                (batch_stats.c.facility_id == StockSnapshot.facility_id)
                & (batch_stats.c.ndc == StockSnapshot.ndc),
            )
            .group_by(
                StockSnapshot.facility_id,
                StockSnapshot.ndc,
                Drug.name,
                ParLevel.reorder_point,
                ParLevel.target_qty,
                batch_stats.c.earliest_expiry,
                batch_stats.c.lots,
            )
        )
        if facility_id is not None:
            stmt = stmt.where(StockSnapshot.facility_id == facility_id)
        if q:
            needle = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    Drug.name.ilike(needle),
                    StockSnapshot.ndc.ilike(needle),
                )
            )

        rows = session.execute(stmt).all()

    payload: list[dict] = []
    for row in rows:
        st, par_defined = derive_status(int(row.quantity), row.reorder_point, row.target_qty)
        if status is not None and st != status:
            continue
        if expiring_before is not None:
            if row.earliest_expiry is None or row.earliest_expiry >= expiring_before:
                continue
        suggested = (
            suggested_order_qty(int(row.quantity), int(row.target_qty))
            if row.target_qty is not None
            else None
        )
        lot = next((value for value in (row.lots or []) if value), None)
        payload.append(
            {
                "ndc": row.ndc,
                "name": row.name,
                "facility_id": row.facility_id,
                "location_id": row.location_id or None,
                "quantity": int(row.quantity),
                "lot": lot,
                "earliest_expiry": row.earliest_expiry.isoformat() if row.earliest_expiry else None,
                "status": st,
                "par_defined": par_defined,
                "reorder_point": row.reorder_point,
                "target_qty": row.target_qty,
                "suggested_qty": suggested,
                "in_formulary": False,
            }
        )

    payload.sort(
        key=lambda item: (
            STATUS_RANK.get(item["status"], 9),
            (item["name"] or item["ndc"]).lower(),
            item["ndc"],
            item["facility_id"] or 0,
        )
    )
    total = len(payload)
    page = payload[offset : offset + limit]
    return {
        "facility_id": facility_id,
        "items": page,
        "limit": limit,
        "offset": offset,
        "total": total,
    }


@api.post("/batches", status_code=201)
def receive_batch(
    body: ReceiveBatchBody,
    principal: Principal = Depends(require("batch:write")),
) -> dict:
    if body.expiry_date < date.today():
        raise HTTPException(status_code=422, detail="expiry_date is in the past")
    with session_scope(principal.hospital_id, principal.user_id) as session:
        fac = _facility(session, body.facility_id)
        if not fac.operated:
            raise HTTPException(status_code=422, detail="facility is not operated")
        existing = session.scalar(
            select(StockBatch).where(
                StockBatch.facility_id == body.facility_id,
                StockBatch.ndc == body.ndc.strip(),
                StockBatch.lot == body.lot.strip(),
            )
        )
        if existing is None:
            row = StockBatch(
                hospital_id=uuid.UUID(principal.hospital_id),
                facility_id=body.facility_id,
                ndc=body.ndc.strip(),
                lot=body.lot.strip(),
                expiry_date=body.expiry_date,
                quantity=body.quantity,
                location_id=body.location_id or "",
            )
            session.add(row)
            session.flush()
        else:
            existing.quantity += body.quantity
            row = existing
            session.flush()
        snapshot = session.scalar(
            select(StockSnapshot).where(
                StockSnapshot.facility_id == row.facility_id,
                StockSnapshot.ndc == row.ndc,
                StockSnapshot.location_id == row.location_id,
            )
        )
        snap_qty = snapshot.quantity if snapshot is not None else row.quantity
        return _batch_dict(row, snap_qty)


@api.get("/batches")
def list_batches(
    ndc: str | None = Query(None),
    facility_id: int | None = Query(None),
    expiring_before: date | None = Query(None),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        if facility_id is not None:
            _facility(session, facility_id)
        stmt = select(StockBatch).order_by(StockBatch.expiry_date.asc(), StockBatch.id.asc())
        if ndc:
            stmt = stmt.where(StockBatch.ndc == ndc.strip())
        if facility_id is not None:
            stmt = stmt.where(StockBatch.facility_id == facility_id)
        if expiring_before is not None:
            stmt = stmt.where(StockBatch.expiry_date < expiring_before)
        rows = session.scalars(stmt).all()
        return {"items": [_batch_dict(r) for r in rows]}


@api.post("/batches/{batch_id}/consume")
def consume_batch(
    batch_id: int,
    body: ConsumeBody,
    principal: Principal = Depends(require("batch:write")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(StockBatch, batch_id)
        if row is None:
            raise HTTPException(status_code=404, detail="batch not found")
        if body.quantity > row.quantity:
            raise HTTPException(status_code=422, detail="quantity exceeds batch on hand")
        row.quantity -= body.quantity
        session.flush()
        snapshot = session.scalar(
            select(StockSnapshot).where(
                StockSnapshot.facility_id == row.facility_id,
                StockSnapshot.ndc == row.ndc,
                StockSnapshot.location_id == row.location_id,
            )
        )
        snap_qty = snapshot.quantity if snapshot is not None else None
        return _batch_dict(row, snap_qty)


@api.get("/par-levels")
def list_par_levels(
    facility_id: int | None = Query(None),
    ndc: str | None = Query(None),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        if facility_id is not None:
            _facility(session, facility_id)
        stmt = select(ParLevel)
        if facility_id is not None:
            stmt = stmt.where(ParLevel.facility_id == facility_id)
        if ndc:
            stmt = stmt.where(ParLevel.ndc == ndc.strip())
        rows = session.scalars(stmt).all()
        return {
            "items": [
                {
                    "id": r.id,
                    "facility_id": r.facility_id,
                    "ndc": r.ndc,
                    "reorder_point": r.reorder_point,
                    "target_qty": r.target_qty,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in rows
            ]
        }


@api.put("/par-levels")
def upsert_par_level(
    body: ParBody,
    principal: Principal = Depends(require("par:write")),
) -> dict:
    if body.target_qty <= body.reorder_point:
        raise HTTPException(status_code=422, detail="target_qty must exceed reorder_point")
    with session_scope(principal.hospital_id, principal.user_id) as session:
        _facility(session, body.facility_id)
        row = session.scalar(
            select(ParLevel).where(
                ParLevel.facility_id == body.facility_id,
                ParLevel.ndc == body.ndc.strip(),
            )
        )
        if row is None:
            row = ParLevel(
                hospital_id=uuid.UUID(principal.hospital_id),
                facility_id=body.facility_id,
                ndc=body.ndc.strip(),
                reorder_point=body.reorder_point,
                target_qty=body.target_qty,
            )
            session.add(row)
        else:
            row.reorder_point = body.reorder_point
            row.target_qty = body.target_qty
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=422, detail="target_qty must exceed reorder_point"
            ) from exc
        return {
            "id": row.id,
            "facility_id": row.facility_id,
            "ndc": row.ndc,
            "reorder_point": row.reorder_point,
            "target_qty": row.target_qty,
        }


@api.delete("/par-levels/{par_id}", status_code=204)
def delete_par_level(
    par_id: int,
    principal: Principal = Depends(require("par:write")),
) -> None:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(ParLevel, par_id)
        if row is None:
            raise HTTPException(status_code=404, detail="par level not found")
        session.delete(row)


app.include_router(api)
app.include_router(api, prefix="/api/inventory")
