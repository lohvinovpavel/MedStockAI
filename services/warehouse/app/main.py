"""Warehouse service (issue #8): facility registry (spec B1), stock by
location, consumption history, storage-condition telemetry and excursions.

Reads only — writes come from ingest (seed_demo) and, later, B4 consume
events. Tenant filtering is session_scope/RLS per the architecture rules; no
hand-written hospital_id predicates (policies themselves are a repo-wide open
item, docs/services.md §8).
"""

import math
import os
from datetime import date, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from medstock_shared.auth import Principal, require
from medstock_shared.db import engine, session_scope
from medstock_shared.models import (
    ConsumptionDaily,
    Drug,
    Facility,
    LocationCondition,
    StockSnapshot,
    StorageLocation,
)
from sqlalchemy import func, or_, select, text

app = FastAPI(title="warehouse")
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
    try:
        semver = pkg_version("medstock-warehouse")
    except PackageNotFoundError:
        semver = "unknown"
    return {"service": "warehouse", "version": os.environ.get("GIT_SHA", "unknown"), "semver": semver}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """B1 rule 3: distance is computed per request, never stored."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _facility_dict(row: Facility) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "type": row.type,
        "lat": float(row.lat) if row.lat is not None else None,
        "lon": float(row.lon) if row.lon is not None else None,
        "operated": row.operated,
    }


@api.get("/facilities")
def list_facilities(
    operated: bool | None = Query(None),
    principal: Principal = Depends(require("facility:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        stmt = select(Facility).order_by(Facility.id)
        if operated is not None:
            stmt = stmt.where(Facility.operated == operated)
        return {"items": [_facility_dict(row) for row in session.scalars(stmt)]}


@api.get("/facilities/{facility_id}")
def get_facility(
    facility_id: int,
    from_id: int | None = Query(None, alias="from"),
    principal: Principal = Depends(require("facility:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(Facility, facility_id)
        if row is None:
            raise HTTPException(status_code=404, detail="facility not found")
        body = _facility_dict(row)
        if from_id is not None:
            origin = session.get(Facility, from_id)
            if origin is None:
                raise HTTPException(status_code=404, detail="origin facility not found")
            if None not in (row.lat, row.lon, origin.lat, origin.lon):
                body["distance_km_from"] = round(
                    _haversine_km(
                        float(origin.lat), float(origin.lon), float(row.lat), float(row.lon)
                    ),
                    1,
                )
        return body


@api.get("/locations")
def list_locations(
    facility_id: int = Query(...),
    principal: Principal = Depends(require("facility:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        if session.get(Facility, facility_id) is None:
            raise HTTPException(status_code=404, detail="facility not found")
        rows = session.scalars(
            select(StorageLocation)
            .where(StorageLocation.facility_id == facility_id)
            .order_by(StorageLocation.id)
        )
        return {
            "items": [
                {"id": r.id, "code": r.code, "name": r.name, "kind": r.kind} for r in rows
            ]
        }


@api.get("/stock")
def get_stock(
    facility_id: int = Query(...),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        if session.get(Facility, facility_id) is None:
            raise HTTPException(status_code=404, detail="facility not found")
        rows = session.execute(
            select(
                StockSnapshot.ndc,
                StockSnapshot.location_id,
                StockSnapshot.quantity,
                Drug.name,
                Drug.storage_class,
            )
            .join(Drug, Drug.ndc == StockSnapshot.ndc, isouter=True)
            .where(StockSnapshot.facility_id == facility_id)
            .order_by(Drug.name, StockSnapshot.ndc)
        )
        return {
            "items": [
                {
                    "ndc": ndc,
                    "name": name,
                    "location": location,
                    "quantity": quantity,
                    "storage_class": storage_class,
                }
                for ndc, location, quantity, name, storage_class in rows
            ]
        }


@api.get("/consumption")
def get_consumption(
    ndc: str = Query(..., min_length=1, max_length=32),
    facility_id: int = Query(...),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        stmt = (
            select(ConsumptionDaily)
            .where(ConsumptionDaily.facility_id == facility_id)
            .where(ConsumptionDaily.ndc == ndc.strip())
            .order_by(ConsumptionDaily.date)
        )
        if date_from is not None:
            stmt = stmt.where(ConsumptionDaily.date >= date_from)
        if date_to is not None:
            stmt = stmt.where(ConsumptionDaily.date <= date_to)
        rows = session.scalars(stmt).all()
        return {
            "ndc": ndc.strip(),
            "rxcui": rows[0].rxcui if rows else None,
            "items": [
                {"date": r.date.isoformat(), "qty": r.qty_consumed, "stockout": r.stockout}
                for r in rows
            ],
        }


@api.get("/locations/{location_id}/conditions")
def get_conditions(
    location_id: int,
    ts_from: datetime | None = Query(None, alias="from"),
    ts_to: datetime | None = Query(None, alias="to"),
    principal: Principal = Depends(require("facility:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        location = session.get(StorageLocation, location_id)
        if location is None:
            raise HTTPException(status_code=404, detail="location not found")
        stmt = (
            select(LocationCondition)
            .where(LocationCondition.location_id == location_id)
            .order_by(LocationCondition.ts)
        )
        if ts_from is not None:
            stmt = stmt.where(LocationCondition.ts >= ts_from)
        if ts_to is not None:
            stmt = stmt.where(LocationCondition.ts <= ts_to)
        rows = session.scalars(stmt).all()
        return {
            "location": {
                "id": location.id,
                "code": location.code,
                "name": location.name,
                "kind": location.kind,
            },
            "items": [
                {
                    "ts": r.ts.isoformat(),
                    "temperature_c": float(r.temperature_c),
                    "humidity_pct": float(r.humidity_pct),
                }
                for r in rows
            ],
        }


@api.get("/excursions")
def get_excursions(
    facility_id: int | None = Query(None),
    principal: Principal = Depends(require("facility:read")),
) -> dict:
    """Storage violations, computed on read: telemetry × stock placement ×
    the drug's class requirements. A misplaced cold-chain drug in a healthy
    room shows up here just like a failing fridge does."""
    temp_breach = or_(
        LocationCondition.temperature_c < Drug.storage_min_c,
        LocationCondition.temperature_c > Drug.storage_max_c,
    )
    humidity_breach = LocationCondition.humidity_pct > Drug.humidity_max_pct
    with session_scope(principal.hospital_id, principal.user_id) as session:
        stmt = (
            select(
                Facility.id.label("facility_id"),
                Facility.code.label("facility"),
                StorageLocation.id.label("location_id"),
                StorageLocation.code.label("location"),
                StorageLocation.kind,
                StockSnapshot.ndc,
                Drug.name,
                Drug.storage_class,
                Drug.storage_min_c,
                Drug.storage_max_c,
                Drug.humidity_max_pct,
                StockSnapshot.quantity,
                func.min(LocationCondition.ts).label("first_ts"),
                func.max(LocationCondition.ts).label("last_ts"),
                func.count().label("hours"),
                func.min(LocationCondition.temperature_c).label("min_temp"),
                func.max(LocationCondition.temperature_c).label("max_temp"),
                func.max(LocationCondition.humidity_pct).label("max_humidity"),
            )
            .join(StorageLocation, StorageLocation.facility_id == Facility.id)
            .join(
                StockSnapshot,
                (StockSnapshot.facility_id == Facility.id)
                & (StockSnapshot.location_id == StorageLocation.code),
            )
            .join(Drug, Drug.ndc == StockSnapshot.ndc)
            .join(LocationCondition, LocationCondition.location_id == StorageLocation.id)
            .where(Drug.storage_class.is_not(None))
            .where(or_(temp_breach, humidity_breach))
            .group_by(
                Facility.id,
                Facility.code,
                StorageLocation.id,
                StorageLocation.code,
                StorageLocation.kind,
                StockSnapshot.ndc,
                Drug.name,
                Drug.storage_class,
                Drug.storage_min_c,
                Drug.storage_max_c,
                Drug.humidity_max_pct,
                StockSnapshot.quantity,
            )
            .order_by(func.count().desc(), StorageLocation.code, StockSnapshot.ndc)
        )
        if facility_id is not None:
            stmt = stmt.where(Facility.id == facility_id)
        items = []
        for row in session.execute(stmt):
            kinds = []
            if float(row.min_temp) < float(row.storage_min_c) or float(row.max_temp) > float(
                row.storage_max_c
            ):
                kinds.append("temperature")
            if float(row.max_humidity) > float(row.humidity_max_pct):
                kinds.append("humidity")
            items.append(
                {
                    "facility_id": row.facility_id,
                    "facility": row.facility,
                    "location_id": row.location_id,
                    "location": row.location,
                    "location_kind": row.kind,
                    "ndc": row.ndc,
                    "drug": row.name,
                    "storage_class": row.storage_class,
                    "required_min_c": float(row.storage_min_c),
                    "required_max_c": float(row.storage_max_c),
                    "required_max_humidity_pct": float(row.humidity_max_pct),
                    "quantity": row.quantity,
                    "first_ts": row.first_ts.isoformat(),
                    "last_ts": row.last_ts.isoformat(),
                    "hours": row.hours,
                    "observed_min_c": float(row.min_temp),
                    "observed_max_c": float(row.max_temp),
                    "observed_max_humidity_pct": float(row.max_humidity),
                    "violations": kinds,
                }
            )
        return {"items": items}


app.include_router(api)
app.include_router(api, prefix="/api/warehouse")
