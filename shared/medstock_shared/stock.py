"""Qualitative hospital-shelf bands from absolute pack counts.

One place for analogue responses and tests. Thresholds are demo-explainable
pack totals, not days-of-supply:

- ``none``:   quantity == 0
- ``low``:    1–20
- ``normal``: 21–100
- ``high``:   quantity > 100
"""

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
