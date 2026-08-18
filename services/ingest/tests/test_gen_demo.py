"""The demo generator's contract (issue #8 → #7).

Three layers, all against the committed artifacts in data/demo:
  1. determinism — regenerating with the fixed seed reproduces the committed
     bytes, so generator code and committed data can never drift apart;
  2. statistical — the planted signals (seasonality, weekend dip, trend,
     stockout censoring, condition excursions) are actually present, pinning
     the contract prediction (issue #7) will train and demo against;
  3. consistency — stock_snapshot quantities agree with the consumption
     history's tail, so the inventory view never contradicts the chart.
"""

import csv
import gzip
import hashlib
import shutil
import statistics
from collections import defaultdict
from datetime import date

import pytest
from app import gen_demo
from app.demo_layout import DEMO_SEED, data_dir

ARTIFACTS = (
    "consumption.csv.gz",
    "stock.csv.gz",
    "conditions.csv.gz",
    "forecast.csv.gz",
    "stock_history.csv.gz",
)


@pytest.fixture(scope="module")
def drugs() -> dict[str, dict]:
    with (data_dir() / "drugs.csv").open() as fh:
        return {row["ndc"]: row for row in csv.DictReader(fh)}


@pytest.fixture(scope="module")
def consumption() -> list[dict]:
    with gzip.open(data_dir() / "consumption.csv.gz", "rt", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def conditions() -> list[dict]:
    with gzip.open(data_dir() / "conditions.csv.gz", "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def test_seed_is_pinned():
    assert DEMO_SEED == 42  # docs/demo-data.md


def test_regeneration_reproduces_committed_artifacts(tmp_path, monkeypatch):
    """Compares the *decompressed* content, not the .gz bytes: identical CSV
    content deflates to different bytes under different zlib builds (CI's
    system 3.12 vs a uv-managed 3.14). Digests keep the assert operands tiny —
    handing pytest two 1.6 MB bytestrings to diff is what once hung CI."""
    committed = data_dir()
    shutil.copy(committed / "drugs.csv", tmp_path / "drugs.csv")
    monkeypatch.setenv("DEMO_DATA_DIR", str(tmp_path))
    gen_demo.run()
    for name in ARTIFACTS:
        regenerated = hashlib.sha256(gzip.decompress((tmp_path / name).read_bytes())).hexdigest()
        stored = hashlib.sha256(gzip.decompress((committed / name).read_bytes())).hexdigest()
        assert regenerated == stored, (
            f"{name} drifted from the generator — rerun `python -m app.gen_demo` and commit"
        )


def _monthly_mean(rows, drugs, cohort, month, facility="central"):
    vals = [
        int(r["qty"])
        for r in rows
        if r["facility"] == facility
        and drugs[r["ndc"]]["cohort"] == cohort
        and r["date"][5:7] == month
    ]
    return statistics.mean(vals)


def test_winter_cohort_peaks_in_january(consumption, drugs):
    jan = _monthly_mean(consumption, drugs, "winter", "01")
    jul = _monthly_mean(consumption, drugs, "winter", "07")
    assert jan > 1.8 * jul


def test_summer_cohort_peaks_in_june(consumption, drugs):
    jun = _monthly_mean(consumption, drugs, "summer", "06")
    dec = _monthly_mean(consumption, drugs, "summer", "12")
    assert jun > 1.4 * dec


def test_weekend_dip(consumption, drugs):
    by_dow = defaultdict(list)
    for r in consumption:
        if r["facility"] != "central":
            continue
        by_dow[date.fromisoformat(r["date"]).weekday()].append(int(r["qty"]))
    weekday = statistics.mean([q for d in range(5) for q in by_dow[d]])
    weekend = statistics.mean(by_dow[5] + by_dow[6])
    assert weekend < 0.65 * weekday


def test_trending_up_grows_year_over_year(consumption, drugs):
    def year_mean(y: str) -> float:
        return statistics.mean(
            int(r["qty"])
            for r in consumption
            if r["facility"] == "central"
            and drugs[r["ndc"]]["cohort"] == "trending_up"
            and r["date"][:4] == y
        )

    assert year_mean("2026") > 1.3 * year_mean("2024")


def test_stockouts_only_on_designated_drugs(consumption, drugs):
    prone = {ndc for ndc, d in drugs.items() if d["stockout_prone"] == "True"}
    flagged = {r["ndc"] for r in consumption if r["stockout"] == "1"}
    assert flagged  # censoring must exist at all
    assert flagged <= prone


def test_amoxicillin_shortage_window_is_censored(consumption, drugs):
    amox = next(ndc for ndc, d in drugs.items() if d["query_name"].startswith("amoxicillin 500"))
    window = [
        r
        for r in consumption
        if r["ndc"] == amox and r["stockout"] == "1" and "2025-12-10" <= r["date"] <= "2026-01-25"
    ]
    assert len(window) >= 7  # the supplier-failure window runs the shelf dry


def test_stock_matches_history_tail(consumption, drugs):
    """stock qty ≈ recent daily mean × the generator's 6–28-day cover window."""
    with gzip.open(data_dir() / "stock.csv.gz", "rt", newline="") as fh:
        stock = {(r["facility"], r["ndc"]): int(r["qty"]) for r in csv.DictReader(fh)}
    tail = defaultdict(list)
    for r in consumption:
        if r["date"] >= "2026-07-18":  # last 28 days of history
            tail[(r["facility"], r["ndc"])].append(int(r["qty"]))
    checked = 0
    for key, qtys in tail.items():
        if drugs[key[1]]["stockout_prone"] == "True":
            continue  # their balance comes from the (s,S) sim, not a cover window
        mean = statistics.mean(qtys)
        if mean == 0:
            continue
        assert key in stock, f"consumed but not stocked: {key}"
        assert 5.0 <= stock[key] / mean <= 29.0, (key, stock[key], mean)
        checked += 1
    assert checked > 300


def test_stock_history_ends_at_the_snapshot():
    """The stock chart's history must meet its projection without a jump:
    the last stock_history day equals stock.csv.gz aggregated per facility."""
    with gzip.open(data_dir() / "stock.csv.gz", "rt", newline="") as fh:
        snapshot = defaultdict(int)
        for r in csv.DictReader(fh):
            snapshot[(r["facility"], r["ndc"])] += int(r["qty"])
    with gzip.open(data_dir() / "stock_history.csv.gz", "rt", newline="") as fh:
        rows = list(csv.DictReader(fh))
    last_day = max(r["date"] for r in rows)
    assert last_day == "2026-08-14"  # demo END_DATE
    checked = 0
    for r in rows:
        if r["date"] != last_day:
            continue
        assert int(r["qty"]) == snapshot[(r["facility"], r["ndc"])], (r["facility"], r["ndc"])
        checked += 1
    assert checked == len(snapshot)


def test_stock_history_never_negative():
    with gzip.open(data_dir() / "stock_history.csv.gz", "rt", newline="") as fh:
        assert all(int(r["qty"]) >= 0 for r in csv.DictReader(fh))


def test_planted_condition_excursions(conditions):
    def series(facility, location):
        return [r for r in conditions if r["facility"] == facility and r["location"] == location]

    fridge = series("central", "fridge-1")
    fail = [float(r["temperature_c"]) for r in fridge if "2026-08-03" <= r["ts"][:10] <= "2026-08-04"]
    normal = [float(r["temperature_c"]) for r in fridge if r["ts"][:10] < "2026-08-03"]
    assert max(fail) > 10.0  # compressor failure planted
    assert max(normal) < 8.0  # and the fridge is healthy otherwise

    bulk = series("warehouse-north", "bulk-room")
    bulk_hot = [r for r in bulk if float(r["temperature_c"]) > 25.0]
    assert 40 <= len(bulk_hot) <= 300  # heatwave afternoons, not a permanent breach
    in_heatwave = [r for r in bulk_hot if "2026-07-18" <= r["ts"][:10] <= "2026-07-24"]
    assert len(in_heatwave) >= 30  # the planted week dominates the breaches
    hottest = max(bulk, key=lambda r: float(r["temperature_c"]))
    assert "2026-07-18" <= hottest["ts"][:10] <= "2026-07-24"

    westend = [
        float(r["humidity_pct"])
        for r in series("westend", "main-room")
        if "2026-07-28" <= r["ts"][:10] <= "2026-08-02"
    ]
    assert max(westend) > 60.0  # humidity breach planted


def test_seed_demo_refuses_outside_demo_environment(monkeypatch):
    from app import seed_demo

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(SystemExit):
        seed_demo.run()
