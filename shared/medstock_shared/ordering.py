"""F3 purchase-order create, pricing snapshot, and status machine.

Used by inventory endpoints and the copilot `draft_order` tool so both paths
write the same rows. No application `WHERE hospital_id` — callers already sit
inside `session_scope`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    Drug,
    Facility,
    PurchaseOrder,
    PurchaseOrderLine,
    ReviewDecision,
    StockBatch,
    Supplier,
    SupplierCatalog,
)
from .pricing import adjust_quantity, money, quote_totals

TRANSITIONS = {
    "draft": {"placed"},
    "placed": {"in_transit", "cancelled"},
    "in_transit": {"delivered", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
}

COMMITTED_STATUSES = ("placed", "in_transit")
COMMITTED_SPEND_DEFINITION = (
    "sum of line totals for placed and in_transit orders"
)


def allocate_po_ref(session: Session, year: int | None = None) -> str:
    n = session.execute(text("SELECT nextval('purchase_order_ref_seq')")).scalar_one()
    return f"PO-{year or date.today().year}-{int(n):04d}"


def _facility_operated(session: Session, facility_id: int) -> Facility:
    row = session.get(Facility, facility_id)
    if row is None:
        raise HTTPException(status_code=404, detail="facility not found")
    if not row.operated:
        raise HTTPException(status_code=422, detail="facility_not_operated")
    return row


def _active_supplier(session: Session, supplier_id: int) -> Supplier:
    row = session.get(Supplier, supplier_id)
    if row is None:
        raise HTTPException(status_code=404, detail="supplier not found")
    if not row.active:
        raise HTTPException(status_code=422, detail="supplier_inactive")
    return row


def snapshot_lines(session: Session, supplier: Supplier, requested: list[dict]) -> tuple[list[dict], dict]:
    """Price lines through F2 at creation time. `requested`: ndc + quantity."""
    catalog = {
        row.ndc: row
        for row in session.scalars(
            select(SupplierCatalog).where(SupplierCatalog.supplier_id == supplier.id)
        )
    }
    quoted: list[dict] = []
    for line in requested:
        ndc = line["ndc"]
        qty = int(line["quantity"])
        row = catalog.get(ndc)
        if row is None:
            raise HTTPException(status_code=422, detail=f"ndc_not_in_catalog: {ndc}")
        rounded, reason = adjust_quantity(qty, int(row.pack_size), int(row.min_order_qty))
        quoted.append(
            {
                "ndc": ndc,
                "requested": qty,
                "rounded_to": rounded,
                "unit_cost": Decimal(row.unit_cost),
                "reason": reason,
            }
        )
    totals = quote_totals(
        lead_time_days=int(supplier.lead_time_days),
        shipping_flat=Decimal(supplier.shipping_flat),
        lines=quoted,
    )
    return quoted, totals


def _order_from_idempotency(session: Session, key: str) -> PurchaseOrder | None:
    return session.scalar(select(PurchaseOrder).where(PurchaseOrder.idempotency_key == key))


def create_purchase_order(
    session: Session,
    *,
    hospital_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    facility_id: int,
    supplier_id: int,
    status: str,
    source: str,
    lines: list[dict],
    note: str | None = None,
    review_decision_id: int | None = None,
    idempotency_key: str | None = None,
) -> PurchaseOrder:
    if status not in ("draft", "placed"):
        raise HTTPException(status_code=422, detail="status must be draft or placed on create")
    if source == "ai_suggestion" and review_decision_id is None:
        raise HTTPException(status_code=422, detail="ai_suggestion requires review_decision_id")
    if source == "manual":
        review_decision_id = None
    if idempotency_key:
        existing = _order_from_idempotency(session, idempotency_key)
        if existing is not None:
            return existing

    _facility_operated(session, facility_id)
    supplier = _active_supplier(session, supplier_id)
    quoted, totals = snapshot_lines(session, supplier, lines)
    now = datetime.now(UTC)
    order = PurchaseOrder(
        ref=allocate_po_ref(session),
        hospital_id=hospital_id,
        facility_id=facility_id,
        supplier_id=supplier_id,
        status=status,
        source=source,
        review_decision_id=review_decision_id,
        shipping=money(Decimal(str(totals["shipping"]))),
        note=note,
        created_by=actor_id,
        placed_at=now if status == "placed" else None,
        expected_delivery=date.fromisoformat(totals["expected_delivery"]),
        idempotency_key=idempotency_key,
    )
    session.add(order)
    session.flush()
    for line in quoted:
        session.add(
            PurchaseOrderLine(
                purchase_order_id=order.id,
                ndc=line["ndc"],
                quantity=int(line["rounded_to"]),
                unit_cost=Decimal(line["unit_cost"]),
            )
        )
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        if idempotency_key:
            existing = _order_from_idempotency(session, idempotency_key)
            if existing is not None:
                return existing
        raise HTTPException(status_code=409, detail="order_conflict") from exc
    return order


def receive_delivered(session: Session, order: PurchaseOrder, lines: list[dict] | None) -> None:
    """Create one stock_batch per PO line. Lot and expiry are required."""
    po_lines = list(
        session.scalars(
            select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == order.id)
        )
    )
    by_ndc = {row.ndc: row for row in po_lines}
    incoming = {row["ndc"]: row for row in (lines or [])}
    missing = [ndc for ndc in by_ndc if ndc not in incoming]
    if missing:
        raise HTTPException(
            status_code=422, detail=f"receive_lines_required: {','.join(missing)}"
        )
    extra = [ndc for ndc in incoming if ndc not in by_ndc]
    if extra:
        raise HTTPException(status_code=422, detail=f"unknown_receive_ndc: {','.join(extra)}")
    for ndc, po_line in by_ndc.items():
        rec = incoming[ndc]
        lot = (rec.get("lot") or "").strip()
        expiry = rec.get("expiry_date")
        if not lot or not expiry:
            raise HTTPException(status_code=422, detail=f"lot_and_expiry_required: {ndc}")
        expiry_date = date.fromisoformat(str(expiry))
        existing = session.scalar(
            select(StockBatch).where(
                StockBatch.facility_id == order.facility_id,
                StockBatch.ndc == ndc,
                StockBatch.lot == lot,
            )
        )
        if existing is not None:
            existing.quantity = int(existing.quantity) + int(po_line.quantity)
            existing.expiry_date = expiry_date
        else:
            session.add(
                StockBatch(
                    hospital_id=order.hospital_id,
                    facility_id=order.facility_id,
                    ndc=ndc,
                    lot=lot,
                    expiry_date=expiry_date,
                    quantity=int(po_line.quantity),
                    location_id="",
                )
            )


def transition_order(
    session: Session,
    order: PurchaseOrder,
    new_status: str,
    receive_lines: list[dict] | None = None,
) -> PurchaseOrder:
    allowed = TRANSITIONS.get(order.status, set())
    if new_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail={"status": order.status, "message": "invalid_transition"},
        )
    now = datetime.now(UTC)
    if new_status == "placed":
        order.placed_at = now
    elif new_status == "delivered":
        receive_delivered(session, order, receive_lines)
        order.delivered_at = now
    elif new_status == "cancelled":
        pass
    order.status = new_status
    session.flush()
    return order


def drug_name(session: Session, ndc: str) -> str:
    row = session.scalar(select(Drug.name).where(Drug.ndc == ndc))
    return row or ndc


def order_line_total(session: Session, order_id: int) -> Decimal:
    value = session.execute(
        select(
            func.coalesce(
                func.sum(PurchaseOrderLine.quantity * PurchaseOrderLine.unit_cost), 0
            )
        ).where(PurchaseOrderLine.purchase_order_id == order_id)
    ).scalar_one()
    return Decimal(value)


def serialize_order(session: Session, order: PurchaseOrder, *, full: bool = False) -> dict:
    lines = list(
        session.scalars(
            select(PurchaseOrderLine)
            .where(PurchaseOrderLine.purchase_order_id == order.id)
            .order_by(PurchaseOrderLine.id)
        )
    )
    facility = session.get(Facility, order.facility_id)
    supplier = session.get(Supplier, order.supplier_id)
    line_sum = sum((Decimal(row.quantity) * Decimal(row.unit_cost) for row in lines), Decimal("0"))
    total = float(money(line_sum + Decimal(order.shipping)))
    first = lines[0] if lines else None
    body = {
        "id": order.id,
        "ref": order.ref,
        "created_at": order.created_at.date().isoformat() if order.created_at else None,
        "facility": {
            "id": facility.id if facility else order.facility_id,
            "code": facility.code if facility else None,
            "name": facility.name if facility else None,
        },
        "supplier": {
            "id": supplier.id if supplier else order.supplier_id,
            "name": supplier.name if supplier else None,
        },
        "status": order.status,
        "source": order.source,
        "line_count": len(lines),
        "primary_drug": drug_name(session, first.ndc) if first else None,
        "quantity": int(first.quantity) if first else 0,
        "total": total,
        "shipping": float(money(Decimal(order.shipping))),
        "expected_delivery": (
            order.expected_delivery.isoformat() if order.expected_delivery else None
        ),
        "note": order.note,
        "review_decision_id": order.review_decision_id,
    }
    if full:
        body["lines"] = [
            {
                "ndc": row.ndc,
                "name": drug_name(session, row.ndc),
                "quantity": int(row.quantity),
                "unit_cost": float(row.unit_cost),
            }
            for row in lines
        ]
        body["placed_at"] = order.placed_at.isoformat() if order.placed_at else None
        body["delivered_at"] = order.delivered_at.isoformat() if order.delivered_at else None
        if order.review_decision_id:
            decision = session.get(ReviewDecision, order.review_decision_id)
            body["review_decision"] = (
                {
                    "id": decision.id,
                    "decision": decision.decision,
                    "payload": decision.payload,
                    "reason": decision.reason,
                }
                if decision
                else None
            )
    return body
