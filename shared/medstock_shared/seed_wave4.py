"""Wave 4 demo seed: partner shortage snapshots (G1) and F2 suppliers.

Called from seed_demo and seed_stock so either path plants the same rows.
Actor GUC / hospital_id must already be set — partner batches fire H1.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .demo_shelf import (
    demo_supplier_rows,
    partner_shortage_consumption_rows,
    partner_shortage_stock_rows,
)
from .models import ConsumptionDaily, StockBatch, StockSnapshot, Supplier, SupplierCatalog


def apply_partner_shortage(session: Session, hospital_id: uuid.UUID, fac_ids: dict[str, int]) -> int:
    rows = partner_shortage_stock_rows(hospital_id, fac_ids)
    if not rows:
        return 0
    ndcs = {row["ndc"] for row in rows}
    partner_ids = {row["facility_id"] for row in rows}
    session.execute(
        delete(StockBatch).where(
            StockBatch.hospital_id == hospital_id,
            StockBatch.ndc.in_(ndcs),
            StockBatch.facility_id.in_(partner_ids),
        )
    )
    session.execute(
        delete(StockSnapshot).where(
            StockSnapshot.hospital_id == hospital_id,
            StockSnapshot.ndc.in_(ndcs),
            StockSnapshot.facility_id.in_(partner_ids),
        )
    )
    session.execute(
        delete(ConsumptionDaily).where(
            ConsumptionDaily.hospital_id == hospital_id,
            ConsumptionDaily.ndc.in_(ndcs),
            ConsumptionDaily.facility_id.in_(partner_ids),
        )
    )
    batches = [
        {
            "hospital_id": hospital_id,
            "facility_id": row["facility_id"],
            "ndc": row["ndc"],
            "lot": row["lot"],
            "expiry_date": datetime.now(tz=UTC).date() + timedelta(days=int(row["expiry_days"])),
            "quantity": int(row["quantity"]),
            "location_id": row["location_id"],
        }
        for row in rows
    ]
    bstmt = insert(StockBatch).values(batches)
    session.execute(
        bstmt.on_conflict_do_update(
            constraint="uq_stock_batch_natural",
            set_={
                "quantity": bstmt.excluded.quantity,
                "location_id": bstmt.excluded.location_id,
                "expiry_date": bstmt.excluded.expiry_date,
            },
        )
    )
    cons = partner_shortage_consumption_rows(hospital_id, fac_ids)
    if cons:
        for i in range(0, len(cons), 500):
            session.execute(insert(ConsumptionDaily), cons[i : i + 500])
    return len(rows)


def apply_suppliers(session: Session, hospital_id: uuid.UUID) -> int:
    suppliers, catalog = demo_supplier_rows(hospital_id)
    if not suppliers:
        return 0
    names = [row["name"] for row in suppliers]
    for row in suppliers:
        values = {
            "hospital_id": hospital_id,
            "name": row["name"],
            "lead_time_days": row["lead_time_days"],
            "reliability_pct": Decimal(row["reliability_pct"]),
            "shipping_flat": Decimal(row["shipping_flat"]),
            "currency": row["currency"],
            "active": row["active"],
        }
        stmt = insert(Supplier).values(**values)
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_supplier_hospital_name",
                set_={
                    "lead_time_days": stmt.excluded.lead_time_days,
                    "reliability_pct": stmt.excluded.reliability_pct,
                    "shipping_flat": stmt.excluded.shipping_flat,
                    "currency": stmt.excluded.currency,
                    "active": stmt.excluded.active,
                },
            )
        )
    id_by_name = {
        name: sid
        for sid, name in session.execute(
            select(Supplier.id, Supplier.name).where(
                Supplier.hospital_id == hospital_id, Supplier.name.in_(names)
            )
        )
    }
    if catalog:
        session.execute(
            delete(SupplierCatalog).where(SupplierCatalog.supplier_id.in_(list(id_by_name.values())))
        )
        payload = [
            {
                "supplier_id": id_by_name[row["supplier_name"]],
                "ndc": row["ndc"],
                "unit_cost": Decimal(row["unit_cost"]),
                "pack_size": row["pack_size"],
                "min_order_qty": row["min_order_qty"],
            }
            for row in catalog
        ]
        session.execute(insert(SupplierCatalog), payload)
    return len(suppliers)
