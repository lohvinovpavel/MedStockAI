"""Run layer: read consumption, fit every series with the shared engine
(medstock_shared.forecasting — also used by ingest's demo generator, so the
committed demo artifact and a live run can never disagree), write one run.

Plain functions over a Session — no FastAPI — so the POST handler, a future
CronJob entrypoint and backtests all call the same code.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from medstock_shared.forecasting import (
    HORIZON_DAYS,
    MIN_HISTORY_DAYS,
    MODEL_VERSION,
    History,
    forecast_series,
    operated_facility_ids,
)
from medstock_shared.models import ConsumptionDaily, ForecastPoint
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

__all__ = ["HORIZON_DAYS", "MIN_HISTORY_DAYS", "MODEL_VERSION", "load_history", "run_forecast"]


def load_history(
    session: Session, as_of: date | None = None
) -> tuple[dict[tuple[int, str], History], date | None]:
    """All consumption series for the session's hospital (RLS/session scope
    decides visibility), capped at `as_of`. Returns (series map, data_through)."""
    # Operated sites only: partner-visibility series (operated = false) are
    # someone else's demand — fitting them would add their forecast points to
    # every hospital-aggregate sum.
    stmt = select(
        ConsumptionDaily.facility_id,
        ConsumptionDaily.ndc,
        ConsumptionDaily.date,
        ConsumptionDaily.qty_consumed,
        ConsumptionDaily.stockout,
    ).where(ConsumptionDaily.facility_id.in_(operated_facility_ids()))
    if as_of is not None:
        stmt = stmt.where(ConsumptionDaily.date <= as_of)
    series: dict[tuple[int, str], History] = defaultdict(dict)
    data_through: date | None = None
    for facility_id, ndc, day, qty, stockout in session.execute(stmt):
        series[(facility_id, ndc)][day] = (qty, stockout)
        if data_through is None or day > data_through:
            data_through = day
    return series, data_through


def run_forecast(
    session: Session, hospital_id: str, as_of: date | None = None, horizon: int = HORIZON_DAYS
) -> dict:
    """Fit every series and write one run. Same-day re-runs replace (E1 rule
    4): any run created today for this hospital is deleted in the same
    transaction the new one is inserted in. Runs from previous days are kept —
    immutability applies across days, not to same-day do-overs."""
    series, data_through = load_history(session, as_of=as_of)
    if data_through is None:
        return {
            "run_id": None,
            "data_through": None,
            "points_written": 0,
            "skus_forecast": 0,
            "skus_skipped": 0,
        }

    run_id = str(uuid.uuid4())
    rows: list[dict] = []
    skipped = 0
    for (facility_id, ndc), history in sorted(series.items()):
        points = forecast_series(history, data_through, horizon=horizon)
        if points is None:
            skipped += 1
            continue
        rows.extend(
            {
                "hospital_id": hospital_id,
                "facility_id": facility_id,
                "ndc": ndc,
                "run_id": run_id,
                "data_through": data_through,
                "target_date": target,
                "p10": p10,
                "p50": p50,
                "p90": p90,
                "model_version": MODEL_VERSION,
            }
            for target, p10, p50, p90 in points
        )

    session.execute(
        delete(ForecastPoint).where(
            ForecastPoint.hospital_id == hospital_id,
            func.date(ForecastPoint.created_at) == func.current_date(),
        )
    )
    if rows:
        session.execute(ForecastPoint.__table__.insert(), rows)
    return {
        "run_id": run_id if rows else None,
        "data_through": data_through.isoformat(),
        "points_written": len(rows),
        "skus_forecast": len(series) - skipped,
        "skus_skipped": skipped,
    }
