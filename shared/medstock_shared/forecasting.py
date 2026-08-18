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

import statistics
from datetime import date, timedelta

MODEL_VERSION = "seasonal_naive_quantile-1"
HORIZON_DAYS = 90
MIN_HISTORY_DAYS = 21  # E1 rule 1: fewer → no forecast row at all
SEASONAL_WEEKS = 4  # p50 = median of last 4 usable same-weekday values
RESIDUAL_WINDOW_DAYS = 84  # 12 weeks of one-step errors feed the band

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
