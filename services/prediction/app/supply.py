"""Days-of-supply — the one place the formula lives (spec E2 rule 4). The
forecast endpoint's depletion line, the at-risk list and the single-SKU
lookup all call this; nothing reimplements it.

Definition: days_of_supply = the smallest d where cumulative forecast p50
demand from data_through+1 through day d reaches quantity on hand. Days are
counted from `data_through` (the last day the run saw) because the stock
snapshot and the forecast share that clock; a stale run answers with its own
dates rather than pretending to know today.

Three distinct honest answers instead of one dishonest number:
- a number, with `basis` naming the arithmetic that produced it
  ("p50" | "p90" | "trailing_mean");
- null + reason "beyond_horizon" — stock outlasts the whole horizon
  (renders "90+", never "unknown");
- null + reason "no_history" — nothing to compute from (renders "unknown",
  never 0 or 999).

Sparse-series guard (grilling round 3): a sub-1/day series can have a
same-weekday median of 0, making cumulative p50 flat zero while the drug
demonstrably moves. If cumulative p50 is 0 but the trailing 28-day mean is
positive, fall back to the trailing mean and say so — the stored forecast
rows stay untouched.
"""

from __future__ import annotations

import math
from datetime import date, timedelta


def scale(value: float, surge_pct: int) -> float:
    """E3 rule 2: a surge multiplies demand, every quantile alike."""
    return value * surge_pct / 100.0


def _depletion_day(
    points: list[tuple[date, float]], on_hand: float
) -> date | None:
    """First target_date where cumulative demand reaches on_hand, else None."""
    cumulative = 0.0
    for target, demand in points:
        cumulative += demand
        if cumulative >= on_hand and cumulative > 0:
            return target
    return None


def summarize(
    quantity: float,
    points: list[tuple[date, float, float, float]],  # (target_date, p10, p50, p90)
    trailing_mean: float | None,
    data_through: date | None,
    surge_pct: int = 100,
    horizon_days: int = 90,
) -> dict:
    """The full days-of-supply verdict for one SKU."""
    out: dict = {
        "days_of_supply": None,
        "days_of_supply_p90": None,
        "depletion_date": None,
        "basis": None,
        "reason": None,
    }

    surged_mean = scale(trailing_mean, surge_pct) if trailing_mean is not None else None

    if points and data_through is not None:
        p50_series = [(d, scale(p50, surge_pct)) for d, _, p50, _ in points]
        p90_series = [(d, scale(p90, surge_pct)) for d, _, _, p90 in points]
        if sum(v for _, v in p50_series) > 0:
            day50 = _depletion_day(p50_series, quantity)
            day90 = _depletion_day(p90_series, quantity)
            out["basis"] = "p50"
            if day50 is None:
                out["reason"] = "beyond_horizon"
            else:
                out["days_of_supply"] = (day50 - data_through).days
                out["depletion_date"] = day50.isoformat()
            if day90 is not None:
                out["days_of_supply_p90"] = (day90 - data_through).days
            return out
        # forecast exists but is flat zero — the sparse-series guard
        if surged_mean and surged_mean > 0:
            return _from_mean(out, quantity, surged_mean, data_through, horizon_days)
        out["basis"] = "p50"
        out["reason"] = "beyond_horizon"
        return out

    # No forecast rows for this SKU (or no run at all): E2's trailing-mean
    # fallback, computed from consumption directly.
    if surged_mean and surged_mean > 0 and data_through is not None:
        return _from_mean(out, quantity, surged_mean, data_through, horizon_days)
    out["reason"] = "no_history"
    return out


def _from_mean(
    out: dict, quantity: float, daily_mean: float, data_through: date, horizon_days: int
) -> dict:
    days = math.ceil(quantity / daily_mean) if quantity > 0 else 0
    out["basis"] = "trailing_mean"
    if days > horizon_days:
        out["reason"] = "beyond_horizon"
    else:
        out["days_of_supply"] = days
        out["days_of_supply_p90"] = days
        out["depletion_date"] = (data_through + timedelta(days=days)).isoformat()
    return out
