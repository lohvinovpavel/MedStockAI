"""Decimal money helpers shared by F2 quotes, F1 restock, and F3 orders."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

TWOPLACES = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def adjust_quantity(requested: int, pack_size: int, min_order_qty: int) -> tuple[int, str | None]:
    """Round up to pack_size and never below min_order_qty. Report why."""
    qty = max(int(requested), int(min_order_qty))
    pack = max(1, int(pack_size))
    remainder = qty % pack
    if remainder:
        qty += pack - remainder
    if qty == requested:
        return qty, None
    if requested < min_order_qty:
        return qty, "min_order_qty" if qty == min_order_qty else "pack_size"
    return qty, "pack_size"


def quote_totals(
    *,
    lead_time_days: int,
    shipping_flat: Decimal,
    lines: list[dict],
    today: date | None = None,
) -> dict:
    """`lines` items: ndc, requested, rounded_to, unit_cost (Decimal), reason|None."""
    subtotal = Decimal(0)
    adjustments = []
    for line in lines:
        unit_cost = Decimal(line["unit_cost"])
        qty = int(line["rounded_to"])
        subtotal += unit_cost * qty
        if line.get("reason"):
            adjustments.append(
                {
                    "ndc": line["ndc"],
                    "requested": int(line["requested"]),
                    "rounded_to": qty,
                    "reason": line["reason"],
                }
            )
    shipping = money(Decimal(shipping_flat))
    subtotal = money(subtotal)
    start = today or datetime.now(tz=UTC).date()
    return {
        "subtotal": float(subtotal),
        "shipping": float(shipping),
        "total": float(money(subtotal + shipping)),
        "lead_time_days": int(lead_time_days),
        "expected_delivery": (start + timedelta(days=int(lead_time_days))).isoformat(),
        "calendar": "calendar_days",
        "adjustments": adjustments,
    }
