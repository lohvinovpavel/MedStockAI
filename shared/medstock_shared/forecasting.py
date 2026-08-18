"""Seasonal-naive quantile forecast engine — spec E1, bottom rung of the
model ladder. p50 is the median of the last 4 same-weekday values; the band
is empirical 10th/90th percentiles of recent one-step residuals, so it is an
honest interval because it is measured from actual error. Climb to
statsforecast only if the multi-window backtest coverage assertion fails.

Lives in shared/ because two packages need the same arithmetic and must
never disagree: `prediction` fits live runs (POST /forecast/runs), and
`ingest`'s demo generator emits the committed forecast artifact the demo
database is seeded from. Pure functions over plain data — no DB, no
FastAPI — so backtests can call them with any historical cutoff.

Stockout-censored days (stockout=true) are excluded from every sample: a
recorded zero on an empty shelf means demand unknown, not demand zero.
Feeding those zeros to the model would teach it that shortages cure demand.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select

from .models import ConsumptionDaily, Drug, ForecastPoint, ShortageEvent, StockSnapshot

MODEL_VERSION = "seasonal_naive_quantile-1"
HORIZON_DAYS = 90
MIN_HISTORY_DAYS = 21  # E1 rule 1: fewer → no forecast row at all
SEASONAL_WEEKS = 4  # p50 = median of last 4 usable same-weekday values
RESIDUAL_WINDOW_DAYS = 84  # 12 weeks of one-step errors feed the band

# Below, the DB-assembly and days-of-supply helpers promoted out of
# services/prediction/app/main.py and supply.py (P3, docs/ai_workflow_impl_plan.md)
# so the copilot's list_at_risk_skus / propose_forecast_rerun tools read the
# exact same thing GET /at-risk and GET /forecast/{rxcui} do.

TRAILING_MEAN_DAYS = 28
HISTORY_DAYS_RETURNED = 60
# A shortage row is active unless its status says otherwise. Status is raw
# feed text and nullable; a shortage row with no status is still a shortage
# row.
RESOLVED_STATUSES = {"resolved", "discontinued"}

# One series is dict[date, (qty, stockout)] for a single (facility, ndc).
History = dict[date, tuple[int, bool]]


def _usable(history: History, day: date) -> int | None:
    """Observed demand on `day`, or None when unobservable (absent/censored)."""
    row = history.get(day)
    if row is None:
        return None
    qty, stockout = row
    return None if stockout else qty


def _same_weekday_values(history: History, before: date, weekday: int) -> list[int]:
    """Up to SEASONAL_WEEKS most recent usable values on `weekday`, strictly
    before `before`, looking back at most twice as far so a censored stretch
    doesn't silently reach into the distant past."""
    values: list[int] = []
    day = before - timedelta(days=1)
    day -= timedelta(days=(day.weekday() - weekday) % 7)
    for _ in range(SEASONAL_WEEKS * 2):
        v = _usable(history, day)
        if v is not None:
            values.append(v)
            if len(values) == SEASONAL_WEEKS:
                break
        day -= timedelta(days=7)
    return values


def _trailing_mean(history: History, before: date, days: int = 28) -> float | None:
    values = [
        v
        for offset in range(1, days + 1)
        if (v := _usable(history, before - timedelta(days=offset))) is not None
    ]
    return statistics.fmean(values) if values else None


def _p50_for(history: History, target: date, anchor: date) -> float | None:
    """Point forecast for `target` using data strictly before `anchor`."""
    values = _same_weekday_values(history, anchor, target.weekday())
    if values:
        return float(statistics.median(values))
    return _trailing_mean(history, anchor)


def _residual_band(history: History, data_through: date) -> tuple[float, float]:
    """(q10, q90) of one-step in-sample residuals over the recent window.
    Falls back to a zero-width band only when no residual is computable —
    which the MIN_HISTORY_DAYS gate makes rare."""
    residuals: list[float] = []
    for offset in range(RESIDUAL_WINDOW_DAYS):
        day = data_through - timedelta(days=offset)
        actual = _usable(history, day)
        if actual is None:
            continue
        predicted = _p50_for(history, day, anchor=day)
        if predicted is None:
            continue
        residuals.append(actual - predicted)
    if len(residuals) < 2:
        return 0.0, 0.0
    deciles = statistics.quantiles(residuals, n=10, method="inclusive")
    return deciles[0], deciles[8]


def forecast_series(
    history: History, data_through: date, horizon: int = HORIZON_DAYS
) -> list[tuple[date, float, float, float]] | None:
    """Forecast one (facility, ndc) series as (target_date, p10, p50, p90)
    tuples. None → insufficient history (E1 rule 1)."""
    if not history:
        return None
    span = (data_through - min(history)).days + 1
    if span < MIN_HISTORY_DAYS:
        return None
    q10, q90 = _residual_band(history, data_through)
    anchor = data_through + timedelta(days=1)
    points = []
    for step in range(1, horizon + 1):
        target = data_through + timedelta(days=step)
        p50 = _p50_for(history, target, anchor=anchor)
        if p50 is None:
            return None
        p50 = max(p50, 0.0)
        p10 = max(p50 + q10, 0.0)
        p90 = max(p50 + q90, 0.0)
        # The CHECK constraint is the law; clip rather than trust float luck.
        p10 = min(p10, p50)
        p90 = max(p90, p50)
        points.append((target, round(p10, 2), round(p50, 2), round(p90, 2)))
    return points


def shortage_active(status: str | None) -> bool:
    return status is None or status.strip().lower() not in RESOLVED_STATUSES


def latest_run(session) -> tuple[str, date, object] | None:
    """(run_id, data_through, created_at) of the newest run, or None."""
    row = session.execute(
        select(ForecastPoint.run_id, ForecastPoint.data_through, ForecastPoint.created_at)
        .order_by(ForecastPoint.created_at.desc())
        .limit(1)
    ).first()
    return (row[0], row[1], row[2]) if row else None


def trailing_means(
    session, ndcs: set[str], through: date, facility_id: int | None
) -> dict[str, float]:
    """Mean daily consumption per NDC over the trailing window, stockout-
    censored days excluded — E2's fallback denominator."""
    stmt = (
        select(ConsumptionDaily.ndc, func.avg(ConsumptionDaily.qty_consumed))
        .where(
            ConsumptionDaily.ndc.in_(ndcs),
            ConsumptionDaily.stockout.is_(False),
            ConsumptionDaily.date > through - timedelta(days=TRAILING_MEAN_DAYS),
            ConsumptionDaily.date <= through,
        )
        .group_by(ConsumptionDaily.ndc)
    )
    if facility_id is not None:
        stmt = stmt.where(ConsumptionDaily.facility_id == facility_id)
    return {ndc: float(mean) for ndc, mean in session.execute(stmt)}


def forecast_by_ndc(
    session, run_id: str, ndcs: set[str], facility_id: int | None
) -> dict[str, list[tuple[date, float, float, float]]]:
    """Latest-run quantiles per NDC, summed across facilities unless one is
    named. Summing quantiles across series is an approximation (a sum of
    medians is not the median of sums) — accepted and published in /ruleset."""
    stmt = (
        select(
            ForecastPoint.ndc,
            ForecastPoint.target_date,
            func.sum(ForecastPoint.p10),
            func.sum(ForecastPoint.p50),
            func.sum(ForecastPoint.p90),
        )
        .where(ForecastPoint.run_id == run_id, ForecastPoint.ndc.in_(ndcs))
        .group_by(ForecastPoint.ndc, ForecastPoint.target_date)
        .order_by(ForecastPoint.ndc, ForecastPoint.target_date)
    )
    if facility_id is not None:
        stmt = stmt.where(ForecastPoint.facility_id == facility_id)
    series: dict[str, list[tuple[date, float, float, float]]] = defaultdict(list)
    for ndc, target, p10, p50, p90 in session.execute(stmt):
        series[ndc].append((target, float(p10), float(p50), float(p90)))
    return series


def on_hand(session, ndcs: set[str] | None, facility_id: int | None) -> dict[str, int]:
    stmt = select(StockSnapshot.ndc, func.sum(StockSnapshot.quantity)).group_by(StockSnapshot.ndc)
    if ndcs is not None:
        stmt = stmt.where(StockSnapshot.ndc.in_(ndcs))
    if facility_id is not None:
        stmt = stmt.where(StockSnapshot.facility_id == facility_id)
    return {ndc: int(qty) for ndc, qty in session.execute(stmt)}


def depletion_fields(verdict: dict) -> dict:
    """The `depletion` object shared by /forecast and the single-SKU lookup."""
    return {
        "date": verdict["depletion_date"],
        "days": verdict["days_of_supply"],
        "days_p90": verdict["days_of_supply_p90"],
        "basis": verdict["basis"],
        "reason": verdict["reason"],
    }


def at_risk_skus(
    session,
    facility_id: int | None,
    within_days: int,
    surge_pct: int,
) -> dict:
    """Every stocked NDC whose days-of-supply falls at or under `within_days`
    -- the query behind `GET /at-risk` and the copilot's list_at_risk_skus
    tool, so the two can never disagree."""
    stock = on_hand(session, None, facility_id)
    run = latest_run(session)
    out: dict = {
        "facility_id": facility_id,
        "within_days": within_days,
        "surge_pct": surge_pct,
        "run_id": run[0] if run else None,
        "generated_at": run[2].isoformat() if run else None,
        "data_through": None,
        "latest_data": None,
        "items": [],
    }
    if not stock:
        return out

    ndcs = set(stock)
    latest_data = session.execute(
        select(func.max(ConsumptionDaily.date)).where(ConsumptionDaily.ndc.in_(ndcs))
    ).scalar()
    data_through = run[1] if run else latest_data
    if data_through is None:
        return out
    out["data_through"] = data_through.isoformat()
    out["latest_data"] = latest_data.isoformat() if latest_data else None

    by_ndc = forecast_by_ndc(session, run[0], ndcs, facility_id) if run else {}
    trailing = trailing_means(session, ndcs, data_through, facility_id)
    names = dict(session.execute(select(Drug.ndc, Drug.name).where(Drug.ndc.in_(ndcs))).all())
    rxcuis = dict(
        session.execute(
            select(ConsumptionDaily.ndc, func.max(ConsumptionDaily.rxcui))
            .where(ConsumptionDaily.ndc.in_(ndcs))
            .group_by(ConsumptionDaily.ndc)
        ).all()
    )
    shortages = {
        ndc
        for ndc, status in session.execute(
            select(ShortageEvent.ndc, ShortageEvent.status).where(ShortageEvent.ndc.in_(ndcs))
        )
        if shortage_active(status)
    }

    items = []
    for ndc, quantity in stock.items():
        verdict = summarize(
            quantity, by_ndc.get(ndc, []), trailing.get(ndc), data_through, surge_pct, HORIZON_DAYS
        )
        days = verdict["days_of_supply"]
        if days is None or days > within_days:
            continue
        items.append(
            {
                "ndc": ndc,
                "rxcui": rxcuis.get(ndc),
                "name": names.get(ndc),
                "quantity": quantity,
                "days_of_supply": days,
                "days_of_supply_p90": verdict["days_of_supply_p90"],
                "depletion_date": verdict["depletion_date"],
                "basis": verdict["basis"],
                "in_shortage": ndc in shortages,
            }
        )
    # E2 rule 3: worst first, ties on quantity then NDC, stable between polls.
    items.sort(key=lambda i: (i["days_of_supply"], i["quantity"], i["ndc"]))
    out["items"] = items
    return out


# --- days-of-supply (promoted from services/prediction/app/supply.py) ------
#
# Definition: days_of_supply = the smallest d where cumulative forecast p50
# demand from data_through+1 through day d reaches quantity on hand. Days are
# counted from `data_through` (the last day the run saw) because the stock
# snapshot and the forecast share that clock.
#
# Three distinct honest answers instead of one dishonest number:
# - a number, with `basis` naming the arithmetic that produced it
#   ("p50" | "p90" | "trailing_mean");
# - null + reason "beyond_horizon" — stock outlasts the whole horizon;
# - null + reason "no_history" — nothing to compute from.


def scale(value: float, surge_pct: int) -> float:
    """E3 rule 2: a surge multiplies demand, every quantile alike."""
    return value * surge_pct / 100.0


def _depletion_day(points: list[tuple[date, float]], on_hand_qty: float) -> date | None:
    """First target_date where cumulative demand reaches on_hand, else None."""
    cumulative = 0.0
    for target, demand in points:
        cumulative += demand
        if cumulative >= on_hand_qty and cumulative > 0:
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
