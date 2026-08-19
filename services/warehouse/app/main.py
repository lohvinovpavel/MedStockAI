"""Warehouse service (issue #8): facility registry (spec B1), stock by
location, consumption history, storage-condition telemetry and excursions.

Reads plus F2 quotes — stock writes come from ingest (seed_demo) and B4
consume events. Tenant filtering is session_scope/RLS; no hand-written
hospital_id predicates.
"""

import math
import os
from datetime import UTC, date, datetime
from decimal import Decimal
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
    Supplier,
    SupplierCatalog,
)
from medstock_shared.warehouse import excursions
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from .pricing import adjust_quantity, quote_totals
from .transfers import transfers

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
                Drug.drug_class,
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
                    "drug_class": drug_class,
                }
                for ndc, location, quantity, name, storage_class, drug_class in rows
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
    room shows up here just like a failing fridge does.

    The query itself lives in `medstock_shared.warehouse` (P2,
    docs/ai_workflow_impl_plan.md) so the copilot's `list_storage_excursions`
    tool can call the same thing this route does."""
    with session_scope(principal.hospital_id, principal.user_id) as session:
        return {"items": excursions(session, facility_id)}


def _require_facility(session, facility_id: int) -> Facility:
    row = session.get(Facility, facility_id)
    if row is None:
        raise HTTPException(status_code=404, detail="facility not found")
    return row


def _supplier_dict(row: Supplier) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "lead_time_days": int(row.lead_time_days),
        "reliability_pct": float(row.reliability_pct),
        "shipping_flat": float(row.shipping_flat),
        "currency": row.currency,
        "active": bool(row.active),
    }


class QuoteLineBody(BaseModel):
    ndc: str = Field(min_length=1, max_length=32)
    quantity: int = Field(gt=0)


class QuoteBody(BaseModel):
    supplier_id: int
    facility_id: int
    lines: list[QuoteLineBody] = Field(min_length=1)


@api.get("/suppliers")
def list_suppliers(
    facility_id: int | None = Query(None),
    principal: Principal = Depends(require("order:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        if facility_id is not None:
            _require_facility(session, facility_id)
        rows = session.scalars(select(Supplier).order_by(Supplier.name, Supplier.id)).all()
        return {"items": [_supplier_dict(row) for row in rows]}


@api.get("/suppliers/{supplier_id}/catalog")
def supplier_catalog(
    supplier_id: int,
    ndc: str | None = Query(None),
    principal: Principal = Depends(require("order:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        supplier = session.get(Supplier, supplier_id)
        if supplier is None:
            raise HTTPException(status_code=404, detail="supplier not found")
        stmt = select(SupplierCatalog).where(SupplierCatalog.supplier_id == supplier_id)
        if ndc:
            stmt = stmt.where(SupplierCatalog.ndc == ndc)
        stmt = stmt.order_by(SupplierCatalog.ndc)
        items = [
            {
                "ndc": row.ndc,
                "unit_cost": float(row.unit_cost),
                "pack_size": int(row.pack_size),
                "min_order_qty": int(row.min_order_qty),
            }
            for row in session.scalars(stmt)
        ]
        return {"supplier_id": supplier_id, "items": items}


@api.post("/quote")
def quote(
    body: QuoteBody,
    principal: Principal = Depends(require("order:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        _require_facility(session, body.facility_id)
        supplier = session.get(Supplier, body.supplier_id)
        if supplier is None:
            raise HTTPException(status_code=404, detail="supplier not found")
        if not supplier.active:
            raise HTTPException(status_code=422, detail="supplier_inactive")
        catalog = {
            row.ndc: row
            for row in session.scalars(
                select(SupplierCatalog).where(SupplierCatalog.supplier_id == supplier.id)
            )
        }
        quoted: list[dict] = []
        for line in body.lines:
            row = catalog.get(line.ndc)
            if row is None:
                raise HTTPException(
                    status_code=422, detail=f"ndc_not_in_catalog: {line.ndc}"
                )
            rounded, reason = adjust_quantity(
                line.quantity, int(row.pack_size), int(row.min_order_qty)
            )
            quoted.append(
                {
                    "ndc": line.ndc,
                    "requested": line.quantity,
                    "rounded_to": rounded,
                    "unit_cost": Decimal(row.unit_cost),
                    "reason": reason,
                }
            )
        return quote_totals(
            lead_time_days=int(supplier.lead_time_days),
            shipping_flat=Decimal(supplier.shipping_flat),
            lines=quoted,
            today=datetime.now(tz=UTC).date(),
        )


app.include_router(api)
app.include_router(api, prefix="/api/warehouse")
app.include_router(transfers)
app.include_router(transfers, prefix="/api/warehouse")
