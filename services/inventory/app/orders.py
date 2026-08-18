"""F3/F4 purchase orders and F1 recommendation writers."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from medstock_shared.auth import Principal, require
from medstock_shared.db import session_scope, set_ai_dedupe_key
from medstock_shared.models import PurchaseOrder, PurchaseOrderLine, ReviewDecision
from medstock_shared.ordering import (
    COMMITTED_SPEND_DEFINITION,
    COMMITTED_STATUSES,
    create_purchase_order,
    serialize_order,
    transition_order,
)
from medstock_shared.pricing import money
from pydantic import BaseModel, Field
from sqlalchemy import func, select

orders = APIRouter()
recommendations = APIRouter()


class OrderLineBody(BaseModel):
    ndc: str = Field(min_length=1, max_length=32)
    quantity: int = Field(gt=0)


class CreateOrderBody(BaseModel):
    facility_id: int
    supplier_id: int
    status: str = "draft"
    source: str = "manual"
    review_decision_id: int | None = None
    lines: list[OrderLineBody] = Field(min_length=1)
    note: str | None = None


class ReceiveLineBody(BaseModel):
    ndc: str = Field(min_length=1, max_length=32)
    lot: str = Field(min_length=1, max_length=64)
    expiry_date: date


class PatchStatusBody(BaseModel):
    status: str
    lines: list[ReceiveLineBody] | None = None


class MaterializeBody(BaseModel):
    facility_id: int
    payload: dict


class RejectBody(BaseModel):
    reason: str = Field(min_length=1)


def _actor_uuid(principal: Principal) -> uuid.UUID | None:
    try:
        return uuid.UUID(principal.user_id)
    except ValueError:
        return None


def _hospital(principal: Principal) -> uuid.UUID:
    return uuid.UUID(principal.hospital_id)


@orders.post("/orders", status_code=201)
def create_order(
    body: CreateOrderBody,
    request: Request,
    principal: Principal = Depends(require("order:write")),
) -> dict:
    idem = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
    with session_scope(principal.hospital_id, principal.user_id) as session:
        order = create_purchase_order(
            session,
            hospital_id=_hospital(principal),
            actor_id=_actor_uuid(principal),
            facility_id=body.facility_id,
            supplier_id=body.supplier_id,
            status=body.status,
            source=body.source,
            lines=[line.model_dump() for line in body.lines],
            note=body.note,
            review_decision_id=body.review_decision_id,
            idempotency_key=idem,
        )
        return serialize_order(session, order, full=True)


@orders.get("/orders/summary")
def orders_summary(
    facility_id: int | None = Query(None),
    principal: Principal = Depends(require("order:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        filters = []
        if facility_id is not None:
            filters.append(PurchaseOrder.facility_id == facility_id)

        def _count(*extra):
            return int(
                session.scalar(
                    select(func.count()).select_from(PurchaseOrder).where(*filters, *extra)
                )
                or 0
            )

        drafts = _count(PurchaseOrder.status == "draft")
        in_transit = _count(PurchaseOrder.status == "in_transit")
        month_start = datetime.now(UTC).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        delivered = _count(
            PurchaseOrder.status == "delivered",
            PurchaseOrder.delivered_at >= month_start,
        )
        line_sum = session.execute(
            select(
                func.coalesce(
                    func.sum(PurchaseOrderLine.quantity * PurchaseOrderLine.unit_cost), 0
                )
            )
            .select_from(PurchaseOrderLine)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .where(*filters, PurchaseOrder.status.in_(COMMITTED_STATUSES))
        ).scalar_one()
        ship_sum = session.execute(
            select(func.coalesce(func.sum(PurchaseOrder.shipping), 0)).where(
                *filters, PurchaseOrder.status.in_(COMMITTED_STATUSES)
            )
        ).scalar_one()
        amount = float(money(Decimal(str(line_sum)) + Decimal(str(ship_sum))))
        return {
            "drafts_awaiting_review": int(drafts or 0),
            "in_transit": int(in_transit or 0),
            "delivered_this_month": int(delivered or 0),
            "timezone": "UTC",
            "committed_spend": {
                "amount": round(amount, 2),
                "currency": "USD",
                "definition": COMMITTED_SPEND_DEFINITION,
            },
        }


@orders.get("/orders")
def list_orders(
    status: list[str] | None = Query(None),
    facility_id: int | None = Query(None),
    supplier_id: int | None = Query(None),
    source: str | None = Query(None),
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require("order:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        stmt = select(PurchaseOrder)
        count_stmt = select(func.count()).select_from(PurchaseOrder)
        if status:
            stmt = stmt.where(PurchaseOrder.status.in_(status))
            count_stmt = count_stmt.where(PurchaseOrder.status.in_(status))
        if facility_id is not None:
            stmt = stmt.where(PurchaseOrder.facility_id == facility_id)
            count_stmt = count_stmt.where(PurchaseOrder.facility_id == facility_id)
        if supplier_id is not None:
            stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
            count_stmt = count_stmt.where(PurchaseOrder.supplier_id == supplier_id)
        if source:
            stmt = stmt.where(PurchaseOrder.source == source)
            count_stmt = count_stmt.where(PurchaseOrder.source == source)
        if from_ is not None:
            stmt = stmt.where(func.date(PurchaseOrder.created_at) >= from_)
            count_stmt = count_stmt.where(func.date(PurchaseOrder.created_at) >= from_)
        if to is not None:
            stmt = stmt.where(func.date(PurchaseOrder.created_at) <= to)
            count_stmt = count_stmt.where(func.date(PurchaseOrder.created_at) <= to)
        total = session.scalar(count_stmt) or 0
        rows = session.scalars(
            stmt.order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "items": [serialize_order(session, row) for row in rows],
        }


@orders.get("/orders/{order_id}")
def get_order(
    order_id: int,
    principal: Principal = Depends(require("order:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        order = session.get(PurchaseOrder, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        return serialize_order(session, order, full=True)


@orders.patch("/orders/{order_id}/status")
def patch_order_status(
    order_id: int,
    body: PatchStatusBody,
    principal: Principal = Depends(require("order:write")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        order = session.get(PurchaseOrder, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        lines = [line.model_dump() for line in body.lines] if body.lines else None
        order = transition_order(session, order, body.status, lines)
        return serialize_order(session, order, full=True)


@orders.delete("/orders/{order_id}", status_code=204)
def delete_order(
    order_id: int,
    principal: Principal = Depends(require("order:write")),
) -> None:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        order = session.get(PurchaseOrder, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        if order.status != "draft":
            raise HTTPException(
                status_code=409,
                detail={"status": order.status, "message": "delete_draft_only"},
            )
        session.delete(order)


def _decision_key(payload: dict) -> str:
    run_id = (payload.get("rationale") or {}).get("run_id")
    ndc = payload.get("ndc") or ""
    return str(run_id or f"restock:{ndc}")


@recommendations.post("/recommendations", status_code=201)
def materialize_recommendation(
    body: MaterializeBody,
    principal: Principal = Depends(require("order:write")),
) -> dict:
    payload = body.payload
    with session_scope(principal.hospital_id, principal.user_id) as session:
        set_ai_dedupe_key(session, _decision_key(payload))
        row = ReviewDecision(
            hospital_id=_hospital(principal),
            facility_id=body.facility_id,
            entity_type="restock_recommendation",
            entity_ref=str(payload.get("ndc") or ""),
            decision="pending",
            actor_id=_actor_uuid(principal),
            payload=payload,
        )
        session.add(row)
        session.flush()
        return {"id": row.id, "decision": row.decision, "payload": row.payload}


def _get_pending(session, rec_id: int) -> ReviewDecision:
    row = session.get(ReviewDecision, rec_id)
    if row is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return row


@recommendations.post("/recommendations/{rec_id}/approve")
def approve_recommendation(
    rec_id: int,
    principal: Principal = Depends(require("recommendation:approve")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = _get_pending(session, rec_id)
        if row.decision != "pending":
            raise HTTPException(
                status_code=409,
                detail={"decision": row.decision, "message": "already_decided"},
            )
        payload = row.payload or {}
        set_ai_dedupe_key(session, _decision_key(payload))
        order = create_purchase_order(
            session,
            hospital_id=_hospital(principal),
            actor_id=_actor_uuid(principal),
            facility_id=row.facility_id,
            supplier_id=int(payload["supplier_id"]),
            status="draft",
            source="ai_suggestion",
            lines=[{"ndc": payload["ndc"], "quantity": int(payload["quantity"])}],
            note=None,
            review_decision_id=row.id,
        )
        row.decision = "approved"
        row.actor_id = _actor_uuid(principal)
        row.decided_at = datetime.now(UTC)
        session.flush()
        return serialize_order(session, order, full=True)


@recommendations.post("/recommendations/{rec_id}/reject")
def reject_recommendation(
    rec_id: int,
    body: RejectBody,
    principal: Principal = Depends(require("recommendation:approve")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = _get_pending(session, rec_id)
        if row.decision != "pending":
            raise HTTPException(
                status_code=409,
                detail={"decision": row.decision, "message": "already_decided"},
            )
        set_ai_dedupe_key(session, _decision_key(row.payload or {}))
        row.decision = "rejected"
        row.reason = body.reason
        row.actor_id = _actor_uuid(principal)
        row.decided_at = datetime.now(UTC)
        session.flush()
        return {"id": row.id, "decision": row.decision, "reason": row.reason}
