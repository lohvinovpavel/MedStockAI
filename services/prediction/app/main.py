"""Prediction service (issue #7): demand forecast (spec E1), days-of-supply
and the at-risk list (E2), surge scenario (E3).

Reads serve stored `forecast_point` rows — GET never fits a model (E1 rule
5). The only thing that computes is the explicit POST /forecast/runs, which
runs the engine synchronously for the caller's hospital; a k8s CronJob can
wrap the same module later. Tenant filtering is session_scope/RLS per the
architecture rules; no hand-written hospital_id predicates.

RxCUI→NDC resolution is local (consumption_daily carries both ids by
design) — no RxNav dependency on the read path.
"""

import os
from collections import defaultdict
from datetime import date, timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, Depends, FastAPI, Query
from medstock_shared.auth import Principal, require
from medstock_shared.db import engine, session_scope
from medstock_shared.forecasting import (
    HISTORY_DAYS_RETURNED,
    HORIZON_DAYS,
    MIN_HISTORY_DAYS,
    MODEL_VERSION,
    RESOLVED_STATUSES,
    TRAILING_MEAN_DAYS,
    at_risk_skus,
    depletion_fields,
    forecast_by_ndc,
    latest_run,
    on_hand,
    operated_facility_ids,
    scale,
    shortage_active,
    summarize,
    trailing_means,
)
from medstock_shared.models import ConsumptionDaily, ShortageEvent, StockDaily
from medstock_shared.restock import compute_recommendations
from pydantic import BaseModel
from sqlalchemy import func, select, text

from .forecast import run_forecast

app = FastAPI(title="prediction")
api = APIRouter()

RULESET_VERSION = "prediction-1"


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependencies checked on purpose —
    a database blip must not get every pod restarted."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/version")
def version() -> dict[str, str]:
    """GIT_SHA is baked in at image build time (Dockerfile) — unset outside
    a built container, e.g. running locally from source. semver comes from
    the installed medstock-prediction package (pyproject.toml), not the image."""
    try:
        semver = pkg_version("medstock-prediction")
    except PackageNotFoundError:
        semver = "unknown"
    return {"service": "prediction", "version": os.environ.get("GIT_SHA", "unknown"), "semver": semver}


@api.get("/forecast/{rxcui}")
def forecast(
    rxcui: str,
    facility_id: int | None = Query(None),
    horizon_days: int = Query(30, ge=1, le=HORIZON_DAYS),
    surge_pct: int = Query(100, ge=100, le=300),
    principal: Principal = Depends(require("forecast:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        ndcs = set(
            session.execute(
                select(ConsumptionDaily.ndc).where(ConsumptionDaily.rxcui == rxcui).distinct()
            ).scalars()
        )

        surged = surge_pct > 100
        out: dict = {
            "rxcui": rxcui,
            "facility_id": facility_id,
            "surge_pct": surge_pct,
            "scenario": "surge" if surged else "standard",
            "run_id": None,
            "model_version": None,
            "generated_at": None,
            "data_through": None,
            "history": [],
            "stock_history": [],
            "forecast": [],
            "depletion": None,
            "reason": None,
        }
        if not ndcs:
            out["reason"] = "no_history"
            return out

        run = latest_run(session)
        # Operated sites only — partner-visibility series (operated = false)
        # must not make the stored run look outrun and trigger a re-run.
        latest_data = session.execute(
            select(func.max(ConsumptionDaily.date)).where(
                ConsumptionDaily.ndc.in_(ndcs),
                ConsumptionDaily.facility_id.in_(operated_facility_ids()),
            )
        ).scalar()
        data_through = run[1] if run else latest_data
        out["data_through"] = data_through.isoformat()
        # What lets a client see that consumption has outrun the run and
        # auto-trigger POST /forecast/runs (grilling: client auto-refresh).
        out["latest_data"] = latest_data.isoformat() if latest_data else None

        hist_stmt = (
            select(ConsumptionDaily.date, func.sum(ConsumptionDaily.qty_consumed))
            .where(
                ConsumptionDaily.ndc.in_(ndcs),
                ConsumptionDaily.date > data_through - timedelta(days=HISTORY_DAYS_RETURNED),
                ConsumptionDaily.date <= data_through,
            )
            .group_by(ConsumptionDaily.date)
            .order_by(ConsumptionDaily.date)
        )
        if facility_id is not None:
            hist_stmt = hist_stmt.where(ConsumptionDaily.facility_id == facility_id)
        else:
            hist_stmt = hist_stmt.where(
                ConsumptionDaily.facility_id.in_(operated_facility_ids())
            )
        out["history"] = [
            {"date": d.isoformat(), "quantity": int(q)} for d, q in session.execute(hist_stmt)
        ]

        # Recorded end-of-day stock, same window as the usage history. Empty
        # until something writes stock_daily (the demo seeder today, B4
        # receiving events eventually) — the chart just shows no line then.
        stock_stmt = (
            select(StockDaily.date, func.sum(StockDaily.qty_on_hand))
            .where(
                StockDaily.ndc.in_(ndcs),
                StockDaily.date > data_through - timedelta(days=HISTORY_DAYS_RETURNED),
                StockDaily.date <= data_through,
            )
            .group_by(StockDaily.date)
            .order_by(StockDaily.date)
        )
        if facility_id is not None:
            stock_stmt = stock_stmt.where(StockDaily.facility_id == facility_id)
        else:
            stock_stmt = stock_stmt.where(StockDaily.facility_id.in_(operated_facility_ids()))
        out["stock_history"] = [
            {"date": d.isoformat(), "quantity": int(q)} for d, q in session.execute(stock_stmt)
        ]

        points: list[tuple[date, float, float, float]] = []
        if run:
            run_id, _, created_at = run
            out["run_id"] = run_id
            out["model_version"] = MODEL_VERSION
            out["generated_at"] = created_at.isoformat()
            merged: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
            for series in forecast_by_ndc(session, run_id, ndcs, facility_id).values():
                for target, p10, p50, p90 in series:
                    merged[target][0] += p10
                    merged[target][1] += p50
                    merged[target][2] += p90
            points = [(t, *merged[t]) for t in sorted(merged)]
            if not points:
                out["reason"] = "insufficient_history"
        else:
            out["reason"] = "no_run"

        out["forecast"] = [
            {
                "date": t.isoformat(),
                "p10": round(scale(p10, surge_pct), 2),
                "p50": round(scale(p50, surge_pct), 2),
                "p90": round(scale(p90, surge_pct), 2),
            }
            for t, p10, p50, p90 in points[:horizon_days]
        ]

        quantity = sum(on_hand(session, ndcs, facility_id).values())
        trailing = trailing_means(session, ndcs, data_through, facility_id)
        trailing_sum = sum(trailing.values()) if trailing else None
        verdict = summarize(quantity, points, trailing_sum, data_through, surge_pct, HORIZON_DAYS)
        out["depletion"] = {"quantity": quantity, **depletion_fields(verdict)}
        if surged:
            baseline = summarize(quantity, points, trailing_sum, data_through, 100, HORIZON_DAYS)
            out["baseline_depletion"] = {
                "date": baseline["depletion_date"],
                "days": baseline["days_of_supply"],
            }
        return out


@api.get("/at-risk")
def at_risk(
    facility_id: int | None = Query(None),
    within_days: int = Query(30, ge=1, le=HORIZON_DAYS),
    surge_pct: int = Query(100, ge=100, le=300),
    principal: Principal = Depends(require("forecast:read")),
) -> dict:
    """The query itself lives in `medstock_shared.forecasting.at_risk_skus`
    (P3, docs/ai_workflow_impl_plan.md) so this route and the copilot's
    list_at_risk_skus tool read the exact same thing. `reorder_point` and a
    per-item `run_id` are this route's own contract additions -- B5 (par
    levels) is unbuilt, so the key ships null so the contract is par-ready."""
    with session_scope(principal.hospital_id, principal.user_id) as session:
        result = at_risk_skus(session, facility_id, within_days, surge_pct)
    result["scenario"] = "surge" if surge_pct > 100 else "standard"
    for item in result["items"]:
        item["reorder_point"] = None
        item["run_id"] = result["run_id"]
    return result


@api.get("/days-of-supply")
def days_of_supply(
    ndc: str = Query(..., min_length=1, max_length=32),
    facility_id: int | None = Query(None),
    surge_pct: int = Query(100, ge=100, le=300),
    principal: Principal = Depends(require("forecast:read")),
) -> dict:
    """Single-SKU form for the inventory row and the analogue dialog (E2)."""
    with session_scope(principal.hospital_id, principal.user_id) as session:
        quantity = on_hand(session, {ndc}, facility_id).get(ndc, 0)
        run = latest_run(session)
        data_through = (
            run[1]
            if run
            else session.execute(
                select(func.max(ConsumptionDaily.date)).where(ConsumptionDaily.ndc == ndc)
            ).scalar()
        )
        points = forecast_by_ndc(session, run[0], {ndc}, facility_id).get(ndc, []) if run else []
        trailing = (
            trailing_means(session, {ndc}, data_through, facility_id).get(ndc)
            if data_through
            else None
        )
        verdict = summarize(quantity, points, trailing, data_through, surge_pct, HORIZON_DAYS)
        status = session.execute(
            select(ShortageEvent.status).where(ShortageEvent.ndc == ndc).limit(1)
        ).first()
        return {
            "ndc": ndc,
            "facility_id": facility_id,
            "quantity": quantity,
            "surge_pct": surge_pct,
            "run_id": run[0] if run else None,
            "generated_at": run[2].isoformat() if run else None,
            "data_through": data_through.isoformat() if data_through else None,
            "days_of_supply": verdict["days_of_supply"],
            "days_of_supply_p90": verdict["days_of_supply_p90"],
            "depletion_date": verdict["depletion_date"],
            "basis": verdict["basis"],
            "reason": verdict["reason"],
            "in_shortage": bool(status) and shortage_active(status[0]),
        }


class RunRequest(BaseModel):
    """`as_of` caps the history a run may see — unset in normal use; pinned
    by the demo pipeline and by backtests."""

    as_of: date | None = None


@api.post("/forecast/runs", status_code=201)
def create_run(
    payload: RunRequest | None = None,
    principal: Principal = Depends(require("forecast:run")),
) -> dict:
    """Fit and store a run for the caller's hospital, synchronously — seconds
    at current scale. Same-day re-runs replace (E1 rule 4)."""
    as_of = payload.as_of if payload else None
    with session_scope(principal.hospital_id, principal.user_id) as session:
        return run_forecast(session, principal.hospital_id, as_of=as_of)


@api.get("/ruleset")
def ruleset(principal: Principal = Depends(require("forecast:read"))) -> dict:
    """Every number the service assumes rather than derives, published —
    the compliance/patient-profiling pattern."""
    return {
        "version": RULESET_VERSION,
        "model_version": MODEL_VERSION,
        "horizon_days": HORIZON_DAYS,
        "min_history_days": MIN_HISTORY_DAYS,
        "trailing_mean_days": TRAILING_MEAN_DAYS,
        "history_days_returned": HISTORY_DAYS_RETURNED,
        "surge_pct_bounds": [100, 300],
        "resolved_shortage_statuses": sorted(RESOLVED_STATUSES),
        "notes": [
            (
                "days_of_supply counts from data_through (the last day the run "
                "saw), not the calendar day of the request — the stock snapshot "
                "and the forecast share that clock."
            ),
            (
                "Hospital-level quantiles are sums of per-facility quantiles — an "
                "approximation (a sum of medians is not the median of sums)."
            ),
            (
                "Stockout-censored days are excluded from all model samples: a "
                "zero on an empty shelf is demand unknown, not demand zero."
            ),
            (
                "reorder_point is null until par levels (spec B5) exist; the "
                "at-risk rule is days-of-supply only until then."
            ),
            (
                "A shortage_event row counts as active unless its status is one "
                "of resolved_shortage_statuses; a row with no status is still a "
                "shortage."
            ),
        ],
    }


@api.get("/recommendations")
def recommendations(
    facility_id: int | None = Query(None),
    surge_pct: int = Query(100, ge=100, le=300),
    ndc: str | None = Query(None),
    principal: Principal = Depends(require("forecast:read")),
) -> dict:
    """F1: computed on read from par + on-hand + F2 catalog. Not stored."""
    with session_scope(principal.hospital_id, principal.user_id) as session:
        return {
            "items": compute_recommendations(
                session, facility_id=facility_id, surge_pct=surge_pct, ndc=ndc
            )
        }


app.include_router(api)
app.include_router(api, prefix="/api/prediction")
