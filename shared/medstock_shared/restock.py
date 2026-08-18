"""F1 restock recommendation — computed on read from B5 + E2 + F2.

A recommendation nobody acted on is not a row. Inventory materialises one as
`review_decision` when the human is shown the card (or on first action).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    ConsumptionDaily,
    Drug,
    ForecastPoint,
    ParLevel,
    StockSnapshot,
    Supplier,
    SupplierCatalog,
    TransferRequest,
)
from .pricing import adjust_quantity, money
from .stock import days_of_supply_from_mean, suggested_order_qty

TRAILING_MEAN_DAYS = 28
COVERAGE_DAYS = 30


def _trailing_mean(session: Session, facility_id: int, ndc: str, through: date) -> float | None:
    value = session.execute(
        select(func.avg(ConsumptionDaily.qty_consumed)).where(
            ConsumptionDaily.facility_id == facility_id,
            ConsumptionDaily.ndc == ndc,
            ConsumptionDaily.stockout.is_(False),
            ConsumptionDaily.date > through - timedelta(days=TRAILING_MEAN_DAYS),
            ConsumptionDaily.date <= through,
        )
    ).scalar()
    return float(value) if value is not None else None


def _on_hand(session: Session, facility_id: int, ndc: str) -> int:
    value = session.execute(
        select(func.coalesce(func.sum(StockSnapshot.quantity), 0)).where(
            StockSnapshot.facility_id == facility_id,
            StockSnapshot.ndc == ndc,
        )
    ).scalar()
    return int(value or 0)


def _latest_run(session: Session, facility_id: int, ndc: str) -> tuple[str, str] | None:
    row = session.execute(
        select(ForecastPoint.run_id, ForecastPoint.model_version)
        .where(ForecastPoint.facility_id == facility_id, ForecastPoint.ndc == ndc)
        .order_by(ForecastPoint.created_at.desc())
        .limit(1)
    ).first()
    return (str(row[0]), str(row[1])) if row else None


def _covering_transfer(session: Session, facility_id: int, ndc: str) -> str | None:
    row = session.scalar(
        select(TransferRequest.ref)
        .where(
            TransferRequest.to_facility_id == facility_id,
            TransferRequest.ndc == ndc,
            TransferRequest.status.in_(("requested", "dispatched")),
        )
        .order_by(TransferRequest.requested_at.desc())
        .limit(1)
    )
    return str(row) if row else None


def _pick_supplier(
    offers: list[tuple[Supplier, SupplierCatalog]],
    days_of_supply: float | None,
) -> tuple[Supplier, SupplierCatalog, bool]:
    """Cheapest whose lead time beats days-of-supply; else fastest, flagged."""
    if days_of_supply is None:
        beating = []
    else:
        beating = [pair for pair in offers if int(pair[0].lead_time_days) < float(days_of_supply)]
    pool = beating or offers
    if beating:
        chosen = min(pool, key=lambda pair: (Decimal(pair[1].unit_cost), int(pair[0].lead_time_days), pair[0].id))
        return chosen[0], chosen[1], False
    chosen = min(pool, key=lambda pair: (int(pair[0].lead_time_days), Decimal(pair[1].unit_cost), pair[0].id))
    return chosen[0], chosen[1], True


def compute_recommendations(
    session: Session,
    *,
    facility_id: int | None,
    surge_pct: int = 100,
    ndc: str | None = None,
) -> list[dict]:
    stmt = select(ParLevel)
    if facility_id is not None:
        stmt = stmt.where(ParLevel.facility_id == facility_id)
    if ndc:
        stmt = stmt.where(ParLevel.ndc == ndc)
    pars = list(session.scalars(stmt.order_by(ParLevel.facility_id, ParLevel.ndc)))
    if not pars:
        return []

    through = date.today()
    items: list[dict] = []
    for par in pars:
        on_hand = _on_hand(session, par.facility_id, par.ndc)
        raw_qty = suggested_order_qty(on_hand, int(par.target_qty))
        if raw_qty <= 0:
            continue
        offers = list(
            session.execute(
                select(Supplier, SupplierCatalog)
                .join(SupplierCatalog, SupplierCatalog.supplier_id == Supplier.id)
                .where(
                    Supplier.active.is_(True),
                    SupplierCatalog.ndc == par.ndc,
                )
            ).all()
        )
        if not offers:
            continue
        mean = _trailing_mean(session, par.facility_id, par.ndc, through)
        dos = days_of_supply_from_mean(on_hand, mean)
        supplier, catalog, lead_risk = _pick_supplier(offers, dos)
        quantity, _reason = adjust_quantity(
            raw_qty, int(catalog.pack_size), int(catalog.min_order_qty)
        )
        unit_cost = Decimal(catalog.unit_cost)
        shipping = money(Decimal(supplier.shipping_flat))
        estimated = money(unit_cost * quantity + shipping)
        run = _latest_run(session, par.facility_id, par.ndc)
        drug = session.scalar(select(Drug).where(Drug.ndc == par.ndc))
        name = drug.name if drug else par.ndc
        transfer_ref = _covering_transfer(session, par.facility_id, par.ndc)
        rationale = {
            "days_of_supply": dos,
            "reorder_point": int(par.reorder_point),
            "target_qty": int(par.target_qty),
            "on_hand": on_hand,
            "surge_pct": surge_pct,
            "run_id": run[0] if run else None,
            "model_version": run[1] if run else None,
            "lead_time_risk": lead_risk,
        }
        if transfer_ref:
            rationale["covered_by_transfer"] = transfer_ref
        items.append(
            {
                "ndc": par.ndc,
                "name": name,
                "facility_id": par.facility_id,
                "quantity": quantity,
                "unit": "units",
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "unit_cost": float(unit_cost),
                "shipping": float(shipping),
                "estimated_total": float(estimated),
                "coverage_days": COVERAGE_DAYS,
                "lead_time_days": int(supplier.lead_time_days),
                "rationale": rationale,
            }
        )
    return items
