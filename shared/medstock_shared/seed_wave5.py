"""Wave 5 demo seed: purchase orders matching DEMO_ORDERS (PO-2026-0141 … 0148).

Called from seed_demo and seed_stock. Actor GUC must already be set (H1).
Does not receive delivered orders into stock — shelf quantities are planted
separately and must not double-count.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, or_, select, text
from sqlalchemy.orm import Session

from .demo_shelf import DASHBOARD_SHELF, DEMO_ORDERS, DEMO_SUPPLIERS
from .models import PurchaseOrder, PurchaseOrderLine, ReviewDecision, Supplier
from .pricing import money


def apply_orders(session: Session, hospital_id: uuid.UUID, fac_ids: dict[str, int]) -> int:
    refs = [row["ref"] for row in DEMO_ORDERS]
    demo_ndcs = {
        item["ndc"] for item in DASHBOARD_SHELF
        if item["id"] in {o["shelf_id"] for o in DEMO_ORDERS if o["source"] == "ai_suggestion"}
    }
    # Found first, scoped to this tenant, and matched against `existing_ids`
    # below by review_decision_id -- not just by demo ref. A stale decision
    # can still be cited by a purchase order this rerun wouldn't otherwise
    # touch (a hand-created order against the same NDC, say), and deleting
    # the decision out from under that order is a FK violation, not a no-op.
    stale_decision_ids = list(
        session.scalars(
            select(ReviewDecision.id).where(
                ReviewDecision.hospital_id == hospital_id,
                ReviewDecision.entity_type == "restock_recommendation",
                ReviewDecision.entity_ref.in_(demo_ndcs),
            )
        )
    )
    po_conditions = [PurchaseOrder.ref.in_(refs)]
    if stale_decision_ids:
        po_conditions.append(PurchaseOrder.review_decision_id.in_(stale_decision_ids))
    existing_ids = list(
        session.scalars(
            select(PurchaseOrder.id).where(
                PurchaseOrder.hospital_id == hospital_id, or_(*po_conditions)
            )
        )
    )
    if existing_ids:
        session.execute(
            delete(PurchaseOrderLine).where(
                PurchaseOrderLine.purchase_order_id.in_(existing_ids)
            )
        )
        session.execute(delete(PurchaseOrder).where(PurchaseOrder.id.in_(existing_ids)))
    if stale_decision_ids:
        session.execute(delete(ReviewDecision).where(ReviewDecision.id.in_(stale_decision_ids)))

    by_shelf = {item["id"]: item for item in DASHBOARD_SHELF}
    cost_by = {
        (spec["name"], shelf_id): Decimal(unit_cost)
        for spec in DEMO_SUPPLIERS
        for shelf_id, unit_cost in spec["catalog"].items()
    }
    ship_by = {spec["name"]: Decimal(spec["shipping_flat"]) for spec in DEMO_SUPPLIERS}
    suppliers = {
        name: sid
        for sid, name in session.execute(
            select(Supplier.id, Supplier.name).where(
                Supplier.name.in_({row["supplier"] for row in DEMO_ORDERS})
            )
        )
    }
    now = datetime.now(UTC)
    planted = 0
    for spec in DEMO_ORDERS:
        item = by_shelf.get(spec["shelf_id"])
        supplier_id = suppliers.get(spec["supplier"])
        facility_id = fac_ids.get(spec["facility"])
        if item is None or supplier_id is None or facility_id is None:
            continue
        unit_cost = cost_by[(spec["supplier"], spec["shelf_id"])]
        created = now - timedelta(days=int(spec["created_days_ago"]))
        expected = (now + timedelta(days=int(spec["expected_days"]))).date()
        decision_id = None
        if spec["source"] == "ai_suggestion":
            decision = ReviewDecision(
                hospital_id=hospital_id,
                facility_id=facility_id,
                entity_type="restock_recommendation",
                entity_ref=item["ndc"],
                decision="approved",
                payload={
                    "ndc": item["ndc"],
                    "name": item["name"],
                    "quantity": spec["quantity"],
                    "supplier_id": supplier_id,
                    "supplier_name": spec["supplier"],
                    "unit_cost": float(unit_cost),
                    "shipping": float(ship_by[spec["supplier"]]),
                    "seed": spec["ref"],
                },
                decided_at=created,
            )
            session.add(decision)
            session.flush()
            decision_id = decision.id
        order = PurchaseOrder(
            ref=spec["ref"],
            hospital_id=hospital_id,
            facility_id=facility_id,
            supplier_id=supplier_id,
            status=spec["status"],
            source=spec["source"],
            review_decision_id=decision_id,
            shipping=money(ship_by[spec["supplier"]]),
            note=spec["note"],
            created_at=created,
            placed_at=created if spec["status"] != "draft" else None,
            expected_delivery=expected,
            delivered_at=created + timedelta(days=6) if spec["status"] == "delivered" else None,
        )
        session.add(order)
        session.flush()
        session.add(
            PurchaseOrderLine(
                purchase_order_id=order.id,
                ndc=item["ndc"],
                quantity=int(spec["quantity"]),
                unit_cost=unit_cost,
            )
        )
        planted += 1
    session.execute(text("SELECT setval('purchase_order_ref_seq', 148, true)"))
    return planted
