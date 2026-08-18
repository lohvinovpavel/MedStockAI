"""G2 transfer endpoints on the warehouse service."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from medstock_shared.auth import Principal, require
from medstock_shared.db import session_scope
from medstock_shared.models import TransferRequest
from medstock_shared.transfers import create_transfer, serialize_transfer, transition_transfer
from pydantic import BaseModel, Field
from sqlalchemy import select

transfers = APIRouter()


class CreateTransferBody(BaseModel):
    from_facility_id: int
    to_facility_id: int
    ndc: str = Field(min_length=1, max_length=32)
    quantity: int = Field(gt=0)
    shortage_id: str | None = None
    note: str | None = None
    partner_source: bool = False


class PatchTransferBody(BaseModel):
    status: str


def _actor(principal: Principal) -> uuid.UUID | None:
    try:
        return uuid.UUID(principal.user_id)
    except ValueError:
        return None


@transfers.post("/transfers", status_code=201)
def post_transfer(
    body: CreateTransferBody,
    principal: Principal = Depends(require("transfer:write")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = create_transfer(
            session,
            hospital_id=uuid.UUID(principal.hospital_id),
            actor_id=_actor(principal),
            from_facility_id=body.from_facility_id,
            to_facility_id=body.to_facility_id,
            ndc=body.ndc,
            quantity=body.quantity,
            shortage_id=body.shortage_id,
            note=body.note,
            partner_source=body.partner_source,
        )
        return serialize_transfer(row)


@transfers.get("/transfers")
def list_transfers(
    facility_id: int | None = Query(None),
    status: str | None = Query(None),
    principal: Principal = Depends(require("inventory:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        stmt = select(TransferRequest).order_by(
            TransferRequest.requested_at.desc(), TransferRequest.id.desc()
        )
        if facility_id is not None:
            stmt = stmt.where(
                (TransferRequest.from_facility_id == facility_id)
                | (TransferRequest.to_facility_id == facility_id)
            )
        if status:
            stmt = stmt.where(TransferRequest.status == status)
        return {"items": [serialize_transfer(row) for row in session.scalars(stmt)]}


@transfers.patch("/transfers/{transfer_id}/status")
def patch_transfer(
    transfer_id: int,
    body: PatchTransferBody,
    principal: Principal = Depends(require("transfer:write")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(TransferRequest, transfer_id)
        if row is None:
            raise HTTPException(status_code=404, detail="transfer not found")
        row = transition_transfer(session, row, body.status)
        return serialize_transfer(row)
