"""Engine and days-of-supply arithmetic — pure, no DB.

The backtest is the one test that makes the quantile band mean anything
(E1 acceptance): fit on history cut before a held-out window, then check the
p10–p90 band covers 0.65–0.95 of the actuals. Three windows on the committed
demo data — flat summer, winter ramp, trending drugs — asserted separately,
because a band tested only in August is a band tested where it can't fail.
"""

import csv
import gzip
import statistics
from datetime import date, timedelta
from pathlib import Path

import pytest
from app.supply import summarize
from medstock_shared.forecasting import MIN_HISTORY_DAYS, forecast_series

DEMO_DIR = Path(__file__).resolve().parents[3] / "data" / "demo"


def flat_history(days: int, qty: int = 10, end: date = date(2026, 8, 14)) -> dict:
    return {end - timedelta(days=i): (qty, False) for i in range(days)}


def test_flat_series_forecasts_flat():
    end = date(2026, 8, 14)
    points = forecast_series(flat_history(60), end, horizon=30)
    assert len(points) == 30
    assert all(p50 == 10 for _, _, p50, _ in points)
    assert all(p10 <= p50 <= p90 for _, p10, p50, p90 in points)


def test_short_history_gets_no_forecast():
    # E1 rule 1: an SKU with 10 days of history produces no rows.
    assert forecast_series(flat_history(10), date(2026, 8, 14), horizon=30) is None
    assert forecast_series(flat_history(MIN_HISTORY_DAYS), date(2026, 8, 14), horizon=5)


def test_stockout_days_are_excluded_from_the_sample():
    end = date(2026, 8, 14)
    history = flat_history(60)
    # Censor the last two weeks as stockout zeros; the median must still be
    # 10, not dragged toward 0 — a zero on an empty shelf is not demand.
    for i in range(14):
        history[end - timedelta(days=i)] = (0, True)
    points = forecast_series(history, end, horizon=7)
    assert all(p50 == 10 for _, _, p50, _ in points)


def test_days_of_supply_flat_ten_a_day():
    # E2 acceptance: 100 on hand, flat 10/day → 10 days.
    end = date(2026, 8, 14)
    points = forecast_series(flat_history(60), end, horizon=90)
    verdict = summarize(100, points, trailing_mean=10.0, data_through=end)
    assert verdict["days_of_supply"] == 10
    assert verdict["basis"] == "p50"
    assert verdict["depletion_date"] == "2026-08-24"


def test_surge_reduces_days_and_never_increases():
    end = date(2026, 8, 14)
    points = forecast_series(flat_history(60), end, horizon=90)
    base = summarize(100, points, 10.0, end, surge_pct=100)
    surged = summarize(100, points, 10.0, end, surge_pct=300)
    assert surged["days_of_supply"] < base["days_of_supply"]


def test_beyond_horizon_is_a_reason_not_a_number():
    end = date(2026, 8, 14)
    points = forecast_series(flat_history(60, qty=1), end, horizon=90)
    verdict = summarize(10_000, points, 1.0, end)
    assert verdict["days_of_supply"] is None
    assert verdict["reason"] == "beyond_horizon"


def test_no_history_is_the_other_null():
    verdict = summarize(50, [], trailing_mean=None, data_through=None)
    assert verdict["days_of_supply"] is None
    assert verdict["reason"] == "no_history"


def test_trailing_mean_fallback_when_no_forecast():
    # E2: no forecast run → still a number, basis says which arithmetic.
    verdict = summarize(100, [], trailing_mean=10.0, data_through=date(2026, 8, 14))
    assert verdict["days_of_supply"] == 10
    assert verdict["basis"] == "trailing_mean"


def test_sparse_series_guard_uses_trailing_mean():
    # A flat-zero forecast with real trailing demand must not claim immortality.
    end = date(2026, 8, 14)
    points = forecast_series(flat_history(60, qty=0), end, horizon=90)
    verdict = summarize(30, points, trailing_mean=0.7, data_through=end)
    assert verdict["basis"] == "trailing_mean"
    assert verdict["days_of_supply"] == 43  # ceil(30 / 0.7)


# --- backtest on the committed demo artifacts ------------------------------


@pytest.fixture(scope="module")
def demo_series() -> dict[tuple[str, str], dict]:
    """(cohort keyed) consumption series at the central facility."""
    with (DEMO_DIR / "drugs.csv").open() as fh:
        cohorts = {r["ndc"]: r["cohort"] for r in csv.DictReader(fh)}
    series: dict[tuple[str, str], dict] = {}
    with gzip.open(DEMO_DIR / "consumption.csv.gz", "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["facility"] != "central":
                continue
            key = (cohorts[row["ndc"]], row["ndc"])
            series.setdefault(key, {})[date.fromisoformat(row["date"])] = (
                int(row["qty"]),
                row["stockout"] == "1",
            )
    return series


def _window_coverage(series: dict, cohort: str, holdout_start: date, n_drugs: int = 8) -> float:
    """Mean share of held-out actuals inside the p10–p90 band, over the first
    `n_drugs` drugs of a cohort and a 30-day window."""
    cut = holdout_start - timedelta(days=1)
    coverages = []
    for (c, _ndc), history in sorted(series.items()):
        if c != cohort or len(coverages) >= n_drugs:
            continue
        fit_history = {d: v for d, v in history.items() if d <= cut}
        points = forecast_series(fit_history, cut, horizon=30)
        if points is None:
            continue
        hits = total = 0
        for target, p10, _p50, p90 in points:
            row = history.get(target)
            if row is None or row[1]:  # absent or stockout-censored
                continue
            total += 1
            hits += p10 <= row[0] <= p90
        if total:
            coverages.append(hits / total)
    assert coverages, f"no usable {cohort} series for window {holdout_start}"
    return statistics.fmean(coverages)


@pytest.mark.parametrize(
    ("cohort", "holdout_start"),
    [
        ("flat", date(2026, 7, 16)),  # easy summer window
        ("winter", date(2025, 12, 15)),  # the ramp seasonal-naive lags into
        ("trending_up", date(2026, 7, 16)),  # trend the median trails behind
    ],
)
def test_backtest_band_coverage(demo_series, cohort, holdout_start):
    coverage = _window_coverage(demo_series, cohort, holdout_start)
    assert 0.65 <= coverage <= 0.95, f"{cohort}@{holdout_start}: coverage {coverage:.2f}"
