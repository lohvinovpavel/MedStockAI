"""Shelf status helpers.

Two vocabularies live here because they answer different questions:

- Analogue rows (UC-4) use pack-count bands: ``none | low | normal | high``.
  Thresholds are demo-explainable pack totals, not days-of-supply.
- Inventory (B5) uses par-relative status: ``stockout | critical | normal |
  surplus``. A missing par row can claim stockout (quantity is a fact) but
  never critical or surplus.
"""

from __future__ import annotations

from typing import Literal

StockStatus = Literal["none", "low", "normal", "high"]

LOW_MAX = 20
NORMAL_MAX = 100


def stock_status(quantity: int) -> StockStatus:
    if quantity <= 0:
        return "none"
    if quantity <= LOW_MAX:
        return "low"
    if quantity <= NORMAL_MAX:
        return "normal"
    return "high"


def stock_fields(quantity: int) -> dict:
    """``quantity``, ``in_stock``, and ``stock_status`` for an analogue row."""
    qty = int(quantity)
    return {
        "quantity": qty,
        "in_stock": qty > 0,
        "stock_status": stock_status(qty),
    }


def derive_status(
    quantity: int,
    reorder_point: int | None,
    target_qty: int | None,
) -> tuple[str, bool]:
    """Return `(status, par_defined)`.

    status ∈ stockout | critical | normal | surplus
    """
    par_defined = reorder_point is not None and target_qty is not None
    if quantity == 0:
        return "stockout", par_defined
    if not par_defined:
        return "normal", False
    if quantity <= reorder_point:
        return "critical", True
    if quantity >= target_qty * 2:
        return "surplus", True
    return "normal", True


def suggested_order_qty(quantity_on_hand: int, target_qty: int) -> int:
    """Units to order to reach target. F1 consumes this; it does not invent its own."""
    return max(0, target_qty - quantity_on_hand)


STATUS_RANK = {"stockout": 0, "critical": 1, "surplus": 2, "normal": 3}
