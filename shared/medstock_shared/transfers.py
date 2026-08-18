"""G2 inter-facility transfer: request, dispatch (FEFO debit), receive, cancel."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import Facility, StockBatch, TransferRequest

TRANSITIONS = {
    "requested": {"dispatched", "cancelled"},
    "dispatched": {"received", "cancelled"},
    "received": set(),
    "cancelled": set(),
}


def allocate_tr_ref(session: Session, year: int | None = None) -> str:
    n = session.execute(text("SELECT nextval('transfer_request_ref_seq')")).scalar_one()
    return f"TR-{year or date.today().year}-{int(n):04d}"


def _get_facility(session: Session, facility_id: int) -> Facility:
    row = session.get(Facility, facility_id)
    if row is None:
        raise HTTPException(status_code=404, detail="facility not found")
    return row


def fefo_batches(session: Session, facility_id: int, ndc: str) -> list[StockBatch]:
    return list(
        session.scalars(
            select(StockBatch)
            .where(
                StockBatch.facility_id == facility_id,
                StockBatch.ndc == ndc,
                StockBatch.quantity > 0,
            )
            .order_by(StockBatch.expiry_date.asc(), StockBatch.id.asc())
            .with_for_update()
        )
    )


def _debit(batches: list[StockBatch], quantity: int) -> list[dict]:
    remaining = int(quantity)
    moved: list[dict] = []
    available = sum(int(b.quantity) for b in batches)
    if available < remaining:
        raise HTTPException(status_code=422, detail="insufficient_source_stock")
    for batch in batches:
        if remaining <= 0:
            break
        take = min(int(batch.quantity), remaining)
        batch.quantity = int(batch.quantity) - take
        remaining -= take
        moved.append(
            {
                "lot": batch.lot,
                "quantity": take,
                "expiry_date": batch.expiry_date.isoformat(),
                "location_id": batch.location_id or "",
            }
        )
    return moved


def _credit(
    session: Session,
    *,
    hospital_id: uuid.UUID,
    facility_id: int,
    ndc: str,
    lots: list[dict],
) -> None:
    for lot in lots:
        existing = session.scalar(
            select(StockBatch)
            .where(
                StockBatch.facility_id == facility_id,
                StockBatch.ndc == ndc,
                StockBatch.lot == lot["lot"],
            )
            .with_for_update()
        )
        qty = int(lot["quantity"])
        expiry = date.fromisoformat(str(lot["expiry_date"]))
        if existing is not None:
            existing.quantity = int(existing.quantity) + qty
            existing.expiry_date = expiry
        else:
            session.add(
                StockBatch(
                    hospital_id=hospital_id,
                    facility_id=facility_id,
                    ndc=ndc,
                    lot=lot["lot"],
                    expiry_date=expiry,
                    quantity=qty,
                    location_id=lot.get("location_id") or "",
                )
            )


def create_transfer(
    session: Session,
    *,
    hospital_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    from_facility_id: int,
    to_facility_id: int,
    ndc: str,
    quantity: int,
    shortage_id: str | None = None,
    note: str | None = None,
    partner_source: bool = False,
) -> TransferRequest:
    if from_facility_id == to_facility_id:
        raise HTTPException(status_code=422, detail="from_and_to_must_differ")
    source = _get_facility(session, from_facility_id)
    dest = _get_facility(session, to_facility_id)
    if not dest.operated:
        raise HTTPException(status_code=422, detail="destination_not_operated")
    if not source.operated and not partner_source:
        raise HTTPException(status_code=422, detail="partner_source_required")
    row = TransferRequest(
        ref=allocate_tr_ref(session),
        hospital_id=hospital_id,
        from_facility_id=from_facility_id,
        to_facility_id=to_facility_id,
        ndc=ndc,
        quantity=int(quantity),
        status="requested",
        shortage_id=shortage_id,
        note=note,
        requested_by=actor_id,
        reserved_lots=[],
    )
    session.add(row)
    session.flush()
    return row


def transition_transfer(session: Session, row: TransferRequest, new_status: str) -> TransferRequest:
    allowed = TRANSITIONS.get(row.status, set())
    if new_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail={"status": row.status, "message": "invalid_transition"},
        )
    now = datetime.now(UTC)
    if new_status == "dispatched":
        batches = fefo_batches(session, row.from_facility_id, row.ndc)
        row.reserved_lots = _debit(batches, int(row.quantity))
        row.dispatched_at = now
    elif new_status == "received":
        _credit(
            session,
            hospital_id=row.hospital_id,
            facility_id=row.to_facility_id,
            ndc=row.ndc,
            lots=list(row.reserved_lots or []),
        )
        row.received_at = now
    elif new_status == "cancelled" and row.status == "dispatched":
        _credit(
            session,
            hospital_id=row.hospital_id,
            facility_id=row.from_facility_id,
            ndc=row.ndc,
            lots=list(row.reserved_lots or []),
        )
        row.reserved_lots = []
    row.status = new_status
    session.flush()
    return row


def serialize_transfer(row: TransferRequest) -> dict:
    return {
        "id": row.id,
        "ref": row.ref,
        "status": row.status,
        "from_facility_id": row.from_facility_id,
        "to_facility_id": row.to_facility_id,
        "ndc": row.ndc,
        "quantity": int(row.quantity),
        "shortage_id": row.shortage_id,
        "note": row.note,
        "requested_at": row.requested_at.isoformat() if row.requested_at else None,
        "dispatched_at": row.dispatched_at.isoformat() if row.dispatched_at else None,
        "received_at": row.received_at.isoformat() if row.received_at else None,
        "lines_reserved": list(row.reserved_lots or []),
    }
