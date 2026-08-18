"""Storage-condition excursions, shared between the warehouse service's
`GET /excursions` and the copilot's `list_storage_excursions` tool
(docs/ai_workflow_impl_plan.md P2).

Computed on read: telemetry x stock placement x the drug's class storage
requirements. A misplaced cold-chain drug in a healthy room shows up here
just like a failing fridge does. Moved out of `services/warehouse/app/main.py`
verbatim -- the query itself is unchanged, only its home.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import Drug, Facility, LocationCondition, StockSnapshot, StorageLocation


def excursions(session: Session, facility_id: int | str | None = None) -> list[dict]:
    if facility_id is not None and isinstance(facility_id, str):
        clean_fid = facility_id.removeprefix("fac-").strip()
        if clean_fid.isdigit():
            facility_id = int(clean_fid)
        else:
            fac_row = session.execute(
                select(Facility.id).where(
                    (Facility.code == facility_id)
                    | (Facility.code == clean_fid)
                    | (Facility.name.ilike(f"%{facility_id}%"))
                )
            ).scalars().first()
            if fac_row is not None:
                facility_id = fac_row

    temp_breach = or_(
        LocationCondition.temperature_c < Drug.storage_min_c,
        LocationCondition.temperature_c > Drug.storage_max_c,
    )
    humidity_breach = LocationCondition.humidity_pct > Drug.humidity_max_pct
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
    return items
