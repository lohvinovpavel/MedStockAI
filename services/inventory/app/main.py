import os
import uuid
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, UploadFile
from medstock_shared.auth import Principal, require
from medstock_shared.db import engine, session_scope
from medstock_shared.formulary import (
    MAX_FORMULARY_BYTES,
    parse_formulary_csv,
    shelf_name_for_rxcui,
    shelf_ndcs_for_rxcuis,
)
from medstock_shared.models import (
    ConsumptionDaily,
    Drug,
    Facility,
    FormularyItem,
    ParLevel,
    ShortageEvent,
    StockBatch,
    StockSnapshot,
)
from medstock_shared.geo import haversine_km
from medstock_shared.rxnorm import RxNormError, ndcs_for_rxcui
from medstock_shared.stock import (
    COVERAGE_RANK,
    STATUS_RANK,
    coverage_band,
    days_of_supply_from_mean,
    derive_status,
    suggested_order_qty,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from .orders import orders, recommendations

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


_RESOLVED_SHORTAGE = {"resolved", "discontinued"}


def _shortage_active(status: str | None) -> bool:
    if status is None:
        return True
    return status.strip().lower() not in _RESOLVED_SHORTAGE


def _local_ndcs_for_rxcuis(session, rxcuis: set[str]) -> dict[str, list[str]]:
    """RxCUI → NDCs without a live NLM round-trip (demo shelf, Drug.raw, consumption)."""
    out: dict[str, list[str]] = {r: [] for r in rxcuis}
    for rxcui, ndcs in shelf_ndcs_for_rxcuis(rxcuis).items():
        for ndc in ndcs:
            if ndc not in out[rxcui]:
                out[rxcui].append(ndc)
    if not rxcuis:
        return {k: v for k, v in out.items() if v}
    for ndc, raw in session.execute(select(Drug.ndc, Drug.raw)).all():
        rxcui = str((raw or {}).get("rxcui") or "")
        if rxcui in out and ndc not in out[rxcui]:
            out[rxcui].append(ndc)
    for ndc, rxcui in session.execute(
        select(ConsumptionDaily.ndc, ConsumptionDaily.rxcui)
        .where(ConsumptionDaily.rxcui.in_(rxcuis))
        .distinct()
    ).all():
        key = str(rxcui)
        if key in out and ndc not in out[key]:
            out[key].append(ndc)
    return {k: v for k, v in out.items() if v}


def _resolve_ndcs_for_rxcuis(session, rxcuis: set[str]) -> dict[str, list[str]]:
    """B3: local Drug.raw / demo_shelf / consumption first, then RxNorm.

    Import rule 5 forbids a live NLM fan-out on a hot path. Calling
    ``ndcs_for_rxcui`` for every formulary row would be that fan-out
    (100+ sequential REST calls per ``GET /exposure``). Unmatched RxCUIs
    still go to the shared client so a real hospital formulary without a
    local NDC map still joins.
    """
    out = _local_ndcs_for_rxcuis(session, rxcuis)
    for rxcui in rxcuis:
        if out.get(rxcui):
            continue
        try:
            remote = ndcs_for_rxcui(rxcui)
        except RxNormError:
            remote = []
        if remote:
            out[rxcui] = list(dict.fromkeys(remote))
    return out


def _formulary_ndc_set(session) -> set[str]:
    rxcuis = set(session.scalars(select(FormularyItem.rxcui)).all())
    ndcs: set[str] = set()
    for values in _local_ndcs_for_rxcuis(session, rxcuis).values():
        ndcs.update(values)
    return ndcs


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
        formulary_ndcs = _formulary_ndc_set(session)
        ndcs = list({row.ndc for row in rows})
        rxcui_by_ndc: dict[str, str | None] = {}
        if ndcs:
            for ndc, raw in session.execute(select(Drug.ndc, Drug.raw).where(Drug.ndc.in_(ndcs))):
                rxcui_by_ndc[str(ndc)] = str((raw or {}).get("rxcui") or "") or None

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
                "in_formulary": row.ndc in formulary_ndcs,
                "rxcui": rxcui_by_ndc.get(row.ndc),
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


@api.post("/formulary/import")
async def import_formulary(
    file: UploadFile = File(...),
    principal: Principal = Depends(require("formulary:write")),
) -> dict:
    """B6: additive CSV upsert of RxCUIs. Name is advisory and is not stored."""
    header = (file.content_type or "").split(";")[0].strip().lower()
    if header and header not in {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"}:
        raise HTTPException(status_code=422, detail="file must be text/csv")
    raw = await file.read(MAX_FORMULARY_BYTES + 1)
    if len(raw) > MAX_FORMULARY_BYTES:
        raise HTTPException(status_code=422, detail="file_too_large")
    try:
        text_body = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="file must be utf-8 csv") from exc
    try:
        rxcuis, rejected = parse_formulary_csv(text_body)
    except ValueError as exc:
        code = str(exc)
        if code == "too_many_rows":
            raise HTTPException(status_code=422, detail="too_many_rows") from exc
        if code == "unrecognised_header":
            raise HTTPException(status_code=422, detail="unrecognised_header") from exc
        raise HTTPException(status_code=422, detail=code) from exc

    hid = uuid.UUID(principal.hospital_id)
    with session_scope(principal.hospital_id, principal.user_id) as session:
        existing = set()
        if rxcuis:
            existing = set(
                session.scalars(select(FormularyItem.rxcui).where(FormularyItem.rxcui.in_(rxcuis))).all()
            )
        inserted = 0
        updated = 0
        now = datetime.now(UTC)
        for rxcui in rxcuis:
            row = session.scalar(select(FormularyItem).where(FormularyItem.rxcui == rxcui))
            if row is None:
                session.add(FormularyItem(hospital_id=hid, rxcui=rxcui))
                inserted += 1
            else:
                row.updated_at = now
                updated += 1
        session.flush()
    return {
        "received": len(rxcuis) + len(rejected),
        "inserted": inserted,
        "updated": updated,
        "rejected": rejected,
    }


@api.get("/formulary")
def list_formulary(
    q: str | None = Query(None),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    needle = (q or "").strip().lower()
    with session_scope(principal.hospital_id, principal.user_id) as session:
        rows = session.scalars(select(FormularyItem).order_by(FormularyItem.rxcui.asc())).all()
        items = []
        for row in rows:
            name = shelf_name_for_rxcui(row.rxcui)
            if needle and needle not in row.rxcui.lower() and needle not in (name or "").lower():
                continue
            items.append(
                {
                    "rxcui": row.rxcui,
                    "name": name,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
            )
        return {"items": items, "total": len(items)}


@api.delete("/formulary/{rxcui}", status_code=204)
def delete_formulary_item(
    rxcui: str,
    principal: Principal = Depends(require("formulary:write")),
) -> None:
    code = rxcui.strip()
    if not code:
        raise HTTPException(status_code=422, detail="rxcui must not be blank")
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.scalar(select(FormularyItem).where(FormularyItem.rxcui == code))
        if row is None:
            raise HTTPException(status_code=404, detail="formulary item not found")
        session.delete(row)


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _map_values_sql(pairs: list[tuple[str, str | None]]) -> str:
    if not pairs:
        return "SELECT NULL::text AS rxcui, NULL::text AS ndc WHERE false"
    parts = []
    for rxcui, ndc in pairs:
        ndc_sql = "NULL::text" if ndc is None else _sql_str(ndc)
        parts.append(f"SELECT {_sql_str(rxcui)} AS rxcui, {ndc_sql} AS ndc")
    return " UNION ALL ".join(parts)


@api.get("/exposure")
def get_exposure(
    facility_id: int | None = Query(None),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    """B3: formulary × stock × shortage. Totals are computed in SQL."""
    with session_scope(principal.hospital_id, principal.user_id) as session:
        if facility_id is not None:
            _facility(session, facility_id)

        formulary_rxcuis = list(session.scalars(select(FormularyItem.rxcui)).all())
        ndc_map = _resolve_ndcs_for_rxcuis(session, set(formulary_rxcuis))

        pairs: list[tuple[str, str | None]] = []
        for rxcui in formulary_rxcuis:
            ndcs = ndc_map.get(rxcui) or [None]
            for ndc in ndcs:
                pairs.append((rxcui, ndc))

        ndcs = [ndc for _, ndc in pairs if ndc]
        stock_stmt = select(
            StockSnapshot.ndc,
            func.coalesce(func.sum(StockSnapshot.quantity), 0).label("quantity"),
        ).group_by(StockSnapshot.ndc)
        if facility_id is not None:
            stock_stmt = stock_stmt.where(StockSnapshot.facility_id == facility_id)
        if ndcs:
            stock_stmt = stock_stmt.where(StockSnapshot.ndc.in_(ndcs))
            stock = {row.ndc: int(row.quantity) for row in session.execute(stock_stmt)}
        else:
            stock = {}

        shortage_rows = []
        if ndcs:
            shortage_rows = session.execute(
                select(ShortageEvent.ndc, ShortageEvent.status, ShortageEvent.source_id)
                .where(ShortageEvent.ndc.in_(ndcs))
                .order_by(ShortageEvent.ndc, ShortageEvent.id.desc())
            ).all()
        shortages: dict[str, tuple[str | None, str]] = {}
        for ndc, status, source_id in shortage_rows:
            if not _shortage_active(status):
                continue
            if ndc not in shortages:
                shortages[ndc] = (status, source_id)

        names: dict[str, str] = {}
        if ndcs:
            names = dict(session.execute(select(Drug.ndc, Drug.name).where(Drug.ndc.in_(ndcs))).all())

        trailing: dict[str, float] = {}
        if ndcs:
            cutoff = date.today() - timedelta(days=28)
            tstmt = (
                select(
                    ConsumptionDaily.ndc,
                    func.avg(ConsumptionDaily.qty_consumed),
                )
                .where(
                    ConsumptionDaily.ndc.in_(ndcs),
                    ConsumptionDaily.date >= cutoff,
                    ConsumptionDaily.stockout.is_(False),
                )
                .group_by(ConsumptionDaily.ndc)
            )
            if facility_id is not None:
                tstmt = tstmt.where(ConsumptionDaily.facility_id == facility_id)
            for ndc, avg_qty in session.execute(tstmt):
                if avg_qty is not None and float(avg_qty) > 0:
                    trailing[ndc] = float(avg_qty)

        map_sql = _map_values_sql(pairs)
        totals_sql = text(
            f"""
            WITH map(rxcui, ndc) AS (
              {map_sql}
            ),
            stock AS (
              SELECT s.ndc, COALESCE(SUM(s.quantity), 0) AS quantity
              FROM stock_snapshot s
              WHERE (:facility_id IS NULL OR s.facility_id = :facility_id)
              GROUP BY s.ndc
            ),
            par AS (
              SELECT p.ndc, MIN(p.reorder_point) AS reorder_point
              FROM par_level p
              WHERE (:facility_id IS NULL OR p.facility_id = :facility_id)
              GROUP BY p.ndc
            ),
            short AS (
              SELECT se.ndc, se.status, se.source_id
              FROM shortage_event se
              WHERE se.status IS NULL
                 OR lower(se.status) NOT IN ('resolved', 'discontinued')
            ),
            per_sku AS (
              SELECT
                f.rxcui,
                BOOL_OR(
                  s_short.source_id IS NOT NULL
                  AND (s_short.status IS NULL
                       OR lower(s_short.status) NOT IN ('resolved', 'discontinued'))
                ) AS in_shortage,
                BOOL_OR(
                  s_short.source_id IS NOT NULL
                  AND (s_short.status IS NULL
                       OR lower(s_short.status) NOT IN ('resolved', 'discontinued'))
                  AND (
                    (par.reorder_point IS NOT NULL
                     AND COALESCE(stock.quantity, 0) <= par.reorder_point)
                    OR (par.reorder_point IS NULL AND COALESCE(stock.quantity, 0) = 0)
                  )
                ) AS uncovered
              FROM formulary_item f
              LEFT JOIN map ON map.rxcui = f.rxcui
              LEFT JOIN stock ON stock.ndc = map.ndc
              LEFT JOIN par ON par.ndc = map.ndc
              LEFT JOIN short s_short ON s_short.ndc = map.ndc
              GROUP BY f.rxcui
            )
            SELECT
              (SELECT COUNT(*) FROM formulary_item)::int AS formulary_skus,
              COUNT(*) FILTER (WHERE in_shortage)::int AS in_shortage,
              COUNT(*) FILTER (WHERE uncovered)::int AS uncovered
            FROM per_sku
            """
        )
        totals_row = session.execute(totals_sql, {"facility_id": facility_id}).one()

        items = []
        for rxcui, ndc in pairs:
            qty = int(stock.get(ndc, 0)) if ndc else 0
            if ndc and ndc in shortages:
                status, source_id = shortages[ndc]
            else:
                status, source_id = None, None
            name = (names.get(ndc) if ndc else None) or shelf_name_for_rxcui(rxcui)
            item = {
                "rxcui": rxcui,
                "ndc": ndc,
                "name": name,
                "quantity": qty,
                "shortage_status": status,
                "shortage_source_id": source_id,
            }
            mean = trailing.get(ndc) if ndc else None
            item["days_of_supply"] = days_of_supply_from_mean(qty, mean)
            items.append(item)

        items.sort(key=lambda row: (row["rxcui"], row["ndc"] or ""))
        return {
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "uncovered_rule": "below_par",
            "facility_id": facility_id,
            "totals": {
                "formulary_skus": int(totals_row[0] or 0),
                "in_shortage": int(totals_row[1] or 0),
                "uncovered": int(totals_row[2] or 0),
            },
            "items": items,
        }


def _relevant_shortage_ndcs(session) -> set[str]:
    """NDCs this tenant stocks or has on formulary — G1 rule 4."""
    formulary_rxcuis = set(session.scalars(select(FormularyItem.rxcui)).all())
    ndc_map = _resolve_ndcs_for_rxcuis(session, formulary_rxcuis)
    ndcs = {ndc for packs in ndc_map.values() for ndc in packs}
    ndcs.update(session.scalars(select(StockSnapshot.ndc).distinct()).all())
    return {ndc for ndc in ndcs if ndc}


def _trailing_mean_by_facility(session, ndcs: set[str]) -> dict[tuple[int, str], float]:
    if not ndcs:
        return {}
    cutoff = date.today() - timedelta(days=28)
    stmt = (
        select(
            ConsumptionDaily.facility_id,
            ConsumptionDaily.ndc,
            func.avg(ConsumptionDaily.qty_consumed),
        )
        .where(
            ConsumptionDaily.ndc.in_(ndcs),
            ConsumptionDaily.date >= cutoff,
            ConsumptionDaily.stockout.is_(False),
        )
        .group_by(ConsumptionDaily.facility_id, ConsumptionDaily.ndc)
    )
    out: dict[tuple[int, str], float] = {}
    for facility_id, ndc, avg_qty in session.execute(stmt):
        if avg_qty is not None and float(avg_qty) > 0:
            out[(int(facility_id), ndc)] = float(avg_qty)
    return out


def _coverage_rows(session, ndc: str, origin: Facility) -> list[dict]:
    facilities = list(session.scalars(select(Facility).order_by(Facility.id)))
    qty_by_fac = {
        int(fid): int(qty)
        for fid, qty in session.execute(
            select(
                StockSnapshot.facility_id,
                func.coalesce(func.sum(StockSnapshot.quantity), 0),
            )
            .where(StockSnapshot.ndc == ndc)
            .group_by(StockSnapshot.facility_id)
        )
        if fid is not None
    }
    trailing = _trailing_mean_by_facility(session, {ndc})
    rows: list[dict] = []
    for fac in facilities:
        has_snapshot = fac.id in qty_by_fac
        if not fac.operated and not has_snapshot:
            continue
        qty = qty_by_fac.get(fac.id, 0)
        days = days_of_supply_from_mean(qty, trailing.get((fac.id, ndc)))
        if fac.id == origin.id:
            distance = 0.0
        elif None not in (fac.lat, fac.lon, origin.lat, origin.lon):
            distance = round(
                haversine_km(
                    float(origin.lat), float(origin.lon), float(fac.lat), float(fac.lon)
                ),
                1,
            )
        else:
            distance = 0.0
        rows.append(
            {
                "facility": {
                    "id": fac.id,
                    "code": fac.code,
                    "name": fac.name,
                    "type": fac.type,
                    "operated": fac.operated,
                },
                "quantity": qty,
                "days_of_supply": days,
                "coverage": coverage_band(qty, days),
                "distance_km": distance,
                "is_current": fac.id == origin.id,
            }
        )
    rows.sort(
        key=lambda row: (
            COVERAGE_RANK.get(row["coverage"], 9),
            row["distance_km"],
            row["facility"]["id"],
        )
    )
    return rows


def _network_stats(rows: list[dict]) -> dict:
    days = [row["days_of_supply"] for row in rows if row["days_of_supply"] is not None]
    return {
        "facilities_affected": sum(
            1 for row in rows if row["coverage"] in ("stockout", "critical")
        ),
        "surplus_facilities": sum(1 for row in rows if row["coverage"] == "surplus"),
        "worst_days_of_supply": min(days) if days else None,
    }


def _alert_payload(session, event: ShortageEvent, origin: Facility | None) -> dict:
    raw = event.raw or {}
    drug = session.scalar(select(Drug).where(Drug.ndc == event.ndc)) if event.ndc else None
    rows = _coverage_rows(session, event.ndc, origin) if origin is not None and event.ndc else []
    agency = raw.get("agency")
    if agency not in ("FDA", "EMA"):
        agency = "EMA" if (event.source_id or "").upper().startswith("EMA") else "FDA"
    rxcui = raw.get("rxcui")
    if not rxcui and drug and isinstance(drug.raw, dict):
        rxcui = drug.raw.get("rxcui")
    updated = event.updated_at
    if updated is not None and updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return {
        "id": event.source_id,
        "ndc": event.ndc,
        "rxcui": rxcui,
        "drug_name": raw.get("name") or (drug.name if drug else event.ndc),
        "status": event.status,
        "source": agency,
        "note": raw.get("note"),
        "updated_at": (
            updated.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if updated
            else None
        ),
        "network": _network_stats(rows),
    }


@api.get("/shortages")
def list_shortages(
    facility_id: int | None = Query(None),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    """G1: open shortage events whose NDC is on this tenant's formulary or shelf."""
    with session_scope(principal.hospital_id, principal.user_id) as session:
        origin = _facility(session, facility_id) if facility_id is not None else None
        if origin is None:
            origin = session.scalars(
                select(Facility).where(Facility.operated.is_(True)).order_by(Facility.id)
            ).first()
        relevant = _relevant_shortage_ndcs(session)
        if not relevant:
            return {"items": []}
        events = session.scalars(
            select(ShortageEvent)
            .where(ShortageEvent.ndc.in_(relevant))
            .order_by(ShortageEvent.source_id)
        ).all()
        items = []
        for event in events:
            if not event.ndc or not _shortage_active(event.status):
                continue
            if event.source_id.startswith("demo-shortage-"):
                continue
            items.append(_alert_payload(session, event, origin))
        items.sort(
            key=lambda row: (
                row["network"]["worst_days_of_supply"]
                if row["network"]["worst_days_of_supply"] is not None
                else 10**9,
                row["id"],
            )
        )
        return {"items": items}


@api.get("/shortages/{source_id}/coverage")
def shortage_coverage(
    source_id: str,
    facility_id: int = Query(...),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    """G1: per-facility coverage for one alert, distances from viewing_from."""
    with session_scope(principal.hospital_id, principal.user_id) as session:
        origin = _facility(session, facility_id)
        event = session.scalar(
            select(ShortageEvent).where(ShortageEvent.source_id == source_id)
        )
        if (
            event is None
            or not event.ndc
            or not _shortage_active(event.status)
            or event.source_id.startswith("demo-shortage-")
        ):
            raise HTTPException(status_code=404, detail="shortage not found")
        if event.ndc not in _relevant_shortage_ndcs(session):
            raise HTTPException(status_code=404, detail="shortage not found")
        return {
            "id": event.source_id,
            "viewing_from": origin.id,
            "rows": _coverage_rows(session, event.ndc, origin),
        }


app.include_router(orders)
app.include_router(orders, prefix="/api/inventory")
app.include_router(recommendations)
app.include_router(recommendations, prefix="/api/inventory")
app.include_router(api)
app.include_router(api, prefix="/api/inventory")
