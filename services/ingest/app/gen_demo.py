"""Generate the demo dataset (issue #8) — deterministic, artifacts committed.

    python -m app.gen_demo

Reads data/demo/drugs.csv (built once by scripts/build_demo_drugs.py, real
RxCUIs/NDCs) and writes three gzipped CSVs next to it:

  consumption.csv.gz  3 years × daily × drug × operated facility
  stock.csv.gz        current on-hand per facility/location/NDC
  conditions.csv.gz   90 days × hourly temp/humidity per storage location
  forecast.csv.gz     one canonical forecast run (issue #7) over the
                      consumption history, data_through = END_DATE, fitted
                      with the same shared engine the prediction service
                      uses live — seeded so a fresh demo DB has a populated
                      forecast chart before anyone presses "Run forecast"
  stock_history.csv.gz  180 days of end-of-day on-hand per facility/NDC,
                      consistent with consumption and ending exactly at
                      stock.csv.gz's snapshot (see gen_stock_history)

Same seed → identical CSV content (gzip mtime pinned to 0 so even the .gz
bytes are stable for one zlib build; different zlib builds deflate to
different bytes, which is why the determinism test compares decompressed
content). A test regenerates and diffs against the committed artifacts, so
generator code and data can't drift apart. Never derive anything from
wall-clock time here.

Planted signals — the contract with prediction (issue #7):
  * weekly profile: weekend dip (Sat 0.55×, Sun 0.50×)
  * annual seasonality: `winter` cohort peaks mid-January, `summer` mid-June
  * trend: `trending_up` +28 %/yr, `trending_down` −18 %/yr
  * demand spikes: one winter outbreak window per winter drug per season
  * stockout censoring: the three stockout_prone drugs run an (s,S) reorder
    simulation with a supplier-failure window each — recorded consumption
    drops below true demand and `stockout` marks the censored days
Planted excursions — the contract with the /excursions endpoint:
  * central fridge-1 fails overnight Aug 3→4 2026 (climbs to ~14 °C)
  * warehouse-north bulk hall (no AC) drifts past 25 °C in the July heatwave
  * westend main room humidity breaches 60 %RH Jul 28–Aug 2 2026
  * one refrigerated drug is shelved in a room (demo_layout.MISPLACED)
"""

from __future__ import annotations

import csv
import gzip
import io
import zlib
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from medstock_shared.forecasting import forecast_series

from .demo_layout import (
    CONDITION_DAYS,
    CONDITION_END,
    DEMO_SEED,
    END_DATE,
    FACILITIES,
    HISTORY_DAYS,
    LOCATIONS,
    MISPLACED,
    data_dir,
    location_for,
)

START_DATE = END_DATE - timedelta(days=HISTORY_DAYS - 1)

# Supplier-failure windows for the stockout-prone drugs, keyed by query_name.
# Deliveries stop inside the window; the (s,S) sim runs dry a few days in.
SHORTAGE_WINDOWS: dict[str, tuple[date, date]] = {
    "amoxicillin 500 MG Oral Capsule": (date(2025, 12, 10), date(2026, 1, 25)),
    "Ventolin HFA 0.09 MG/ACTUAT Metered Dose Inhaler": (date(2026, 3, 5), date(2026, 4, 10)),
    "Ozempic 2 MG/1.5 ML Pen Injector": (date(2025, 6, 1), date(2025, 8, 5)),
}

# Fridge failure: central fridge-1, overnight Aug 3→4 2026.
FRIDGE_FAIL_START = np.datetime64("2026-08-03T22:00")
FRIDGE_FAIL_END = np.datetime64("2026-08-04T08:00")
# Heatwave afternoons push the non-AC bulk hall past 25 °C.
HEATWAVE_START = np.datetime64("2026-07-18")
HEATWAVE_END = np.datetime64("2026-07-25")
# Humidity breach: westend main room.
HUMIDITY_BREACH_START = np.datetime64("2026-07-28")
HUMIDITY_BREACH_END = np.datetime64("2026-08-03")

REORDER_LEAD_DAYS = 3
REORDER_COVER_S = 30  # order-up-to level, days of mean demand
REORDER_POINT_s = 10  # reorder point, days of mean demand


def _rng(*key: str) -> np.random.Generator:
    """Independent, stable stream per (purpose, facility, drug, ...) key —
    adding a drug or facility never shifts any other stream's draws."""
    crc = zlib.crc32("|".join(key).encode())
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence([DEMO_SEED, crc])))


def load_drugs(path: Path) -> list[dict]:
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["base_daily"] = float(row["base_daily"])
        row["stockout_prone"] = row["stockout_prone"] == "True"
    return rows


def _seasonal(doy: np.ndarray, peak_doy: float, amplitude: float) -> np.ndarray:
    """1 + amplitude * squared-cosine bump centered on peak_doy."""
    bump = (1.0 + np.cos(2.0 * np.pi * (doy - peak_doy) / 365.25)) / 2.0
    return 1.0 + amplitude * bump**2


def demand_series(drug: dict, facility: dict, n_days: int) -> np.ndarray:
    """True daily demand for one drug at one facility (before censoring)."""
    rng = _rng("demand", facility["code"], drug["ndc"])
    days = np.arange(n_days)
    dates = np.datetime64(START_DATE) + days
    doy = (dates - dates.astype("datetime64[Y]")).astype(int) + 1
    dow = (START_DATE.weekday() + days) % 7

    weekly = np.where(dow == 5, 0.55, np.where(dow == 6, 0.50, 1.0))

    cohort = drug["cohort"]
    if cohort == "winter":
        seasonal = _seasonal(doy, peak_doy=15, amplitude=1.1)
    elif cohort == "summer":
        seasonal = _seasonal(doy, peak_doy=166, amplitude=0.8)
    else:
        seasonal = _seasonal(doy, peak_doy=15, amplitude=0.06)

    t_years = days / 365.25
    if cohort == "trending_up":
        trend = (1.0 + 0.28) ** t_years
    elif cohort == "trending_down":
        trend = (1.0 - 0.18) ** t_years
    else:
        trend = 1.0 + 0.0  # flat

    spikes = np.ones(n_days)
    if cohort == "winter":
        # One outbreak window per winter season, near the seasonal peak.
        for year in range(START_DATE.year, END_DATE.year + 1):
            peak = date(year, 1, 15)
            if not (START_DATE <= peak <= END_DATE):
                continue
            offset = int(rng.integers(-20, 21))
            length = int(rng.integers(7, 15))
            mult = float(rng.uniform(1.4, 2.2))
            start_idx = (peak - START_DATE).days + offset
            spikes[max(start_idx, 0) : max(start_idx, 0) + length] *= mult
    elif rng.random() < 0.25:
        start_idx = int(rng.integers(0, n_days - 10))
        spikes[start_idx : start_idx + int(rng.integers(5, 11))] *= float(rng.uniform(1.3, 1.8))

    mean = drug["base_daily"] * facility["scale"] * weekly * seasonal * trend * spikes
    # Overdispersed counts: gamma-mixed Poisson (negative binomial-ish).
    lam = mean * rng.gamma(6.0, 1.0 / 6.0, size=n_days)
    return rng.poisson(lam).astype(np.int64)


def censor_stockouts(
    drug: dict, facility: dict, demand: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(s,S) reorder simulation. Returns (recorded, stockout_flags,
    end-of-day on_hand series — its last element is the current balance).

    Deliveries stop inside the drug's SHORTAGE_WINDOWS entry, so on-hand runs
    dry and recorded consumption < true demand — the censoring prediction (#7)
    must learn to see through.
    """
    window = SHORTAGE_WINDOWS.get(drug["query_name"])
    mean_daily = float(demand.mean())
    order_up_to = max(round(mean_daily * REORDER_COVER_S), 1)
    reorder_at = max(round(mean_daily * REORDER_POINT_s), 1)

    on_hand = order_up_to
    pending: list[tuple[int, int]] = []  # (arrival_day, qty)
    recorded = np.zeros_like(demand)
    stockout = np.zeros(len(demand), dtype=bool)
    on_hand_series = np.zeros_like(demand)
    for i in range(len(demand)):
        day = START_DATE + timedelta(days=i)
        arrived = [qty for arrive, qty in pending if arrive <= i]
        pending = [(arrive, qty) for arrive, qty in pending if arrive > i]
        on_hand += sum(arrived)
        served = min(int(demand[i]), on_hand)
        recorded[i] = served
        stockout[i] = served < int(demand[i])
        on_hand -= served
        on_hand_series[i] = on_hand
        in_window = window is not None and window[0] <= day <= window[1]
        if on_hand <= reorder_at and not pending and not in_window:
            pending.append((i + REORDER_LEAD_DAYS, order_up_to - on_hand))
    return recorded, stockout, on_hand_series


def _write_gz(path: Path, header: list[str], rows) -> int:
    """Deterministic gzip (mtime=0, no filename) so reruns are byte-identical."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        writer = csv.writer(text)
        writer.writerow(header)
        count = 0
        for row in rows:
            writer.writerow(row)
            count += 1
        text.flush()
        text.detach()
    path.write_bytes(buf.getvalue())
    return count


def gen_consumption(
    drugs: list[dict],
) -> tuple[list, dict[tuple[str, str], int], dict[tuple[str, str], np.ndarray]]:
    """All consumption rows + ending on-hand per (facility, ndc) + the prone
    drugs' true daily on-hand series (their balance comes from the sim, so
    the stock-history artifact reuses it verbatim)."""
    rows: list = []
    end_stock: dict[tuple[str, str], int] = {}
    prone_on_hand: dict[tuple[str, str], np.ndarray] = {}
    dates = [START_DATE + timedelta(days=i) for i in range(HISTORY_DAYS)]
    date_strs = [d.isoformat() for d in dates]
    for facility in FACILITIES:
        if facility["scale"] is None:
            continue
        for drug in drugs:
            if location_for(facility["code"], drug["storage_class"]) is None:
                continue
            demand = demand_series(drug, facility, HISTORY_DAYS)
            if drug["stockout_prone"]:
                recorded, flags, on_hand_series = censor_stockouts(drug, facility, demand)
                on_hand = int(on_hand_series[-1])
                prone_on_hand[(facility["code"], drug["ndc"])] = on_hand_series
            else:
                recorded, flags = demand, np.zeros(HISTORY_DAYS, dtype=bool)
                rng = _rng("stock", facility["code"], drug["ndc"])
                on_hand = round(float(recorded[-28:].mean()) * rng.uniform(6.0, 28.0))
            end_stock[(facility["code"], drug["ndc"])] = on_hand
            code = facility["code"]
            ndc, rxcui = drug["ndc"], drug["rxcui"]
            for i in range(HISTORY_DAYS):
                rows.append(
                    (code, ndc, rxcui, date_strs[i], int(recorded[i]), int(flags[i]))
                )
    return rows, end_stock, prone_on_hand


def gen_forecast(consumption: list) -> list:
    """The committed forecast run: fit every (facility, ndc) series exactly
    as POST /forecast/runs would, cut at END_DATE. Deterministic because the
    engine is pure and the input is the seeded consumption sim."""
    series: dict[tuple[str, str], dict] = {}
    for code, ndc, _rxcui, date_str, qty, stockout in consumption:
        series.setdefault((code, ndc), {})[date.fromisoformat(date_str)] = (qty, bool(stockout))
    rows: list = []
    for (code, ndc), history in sorted(series.items()):
        points = forecast_series(history, END_DATE)
        if points is None:
            continue
        rows.extend(
            (code, ndc, target.isoformat(), p10, p50, p90) for target, p10, p50, p90 in points
        )
    return rows


STOCK_HISTORY_DAYS = 180  # what the stock chart draws; consumption keeps 3y


def gen_stock_history(
    stock: list,
    consumption: list,
    prone_on_hand: dict[tuple[str, str], np.ndarray],
    drugs: list[dict],
) -> list:
    """End-of-day on-hand per (facility, ndc), last STOCK_HISTORY_DAYS days.

    Three cases, all ending exactly at the committed snapshot so the chart's
    history meets its projection without a jump:
    - stockout-prone: the (s,S) sim's true daily balance, verbatim;
    - other operated drugs: reconstructed *backwards* from the committed end
      stock — adding back each day's consumption, and when the walk exceeds
      the (s,S) order-up-to cap, inserting a delivery that drops the earlier
      balance near the reorder point. Backwards, because the committed
      snapshot (and its cover-window invariant the tests pin) must not
      change; a forward sim would land somewhere else.
    - partner facilities (no consumption recorded): a flat line, so
      hospital-wide sums stay continuous at the boundary.
    """
    prone_ndcs = {d["ndc"] for d in drugs if d["stockout_prone"]}
    stock_by_key: dict[tuple[str, str], int] = {}
    for code, _location, ndc, qty in stock:
        stock_by_key[(code, ndc)] = stock_by_key.get((code, ndc), 0) + int(qty)
    consumed: dict[tuple[str, str], list[int]] = {}
    for code, ndc, _rxcui, _date, qty, _flag in consumption:
        consumed.setdefault((code, ndc), []).append(int(qty))

    dates = [
        (END_DATE - timedelta(days=STOCK_HISTORY_DAYS - 1 - i)).isoformat()
        for i in range(STOCK_HISTORY_DAYS)
    ]
    rows: list = []
    for (code, ndc), end_qty in sorted(stock_by_key.items()):
        if ndc in prone_ndcs and (code, ndc) in prone_on_hand:
            qtys = [int(q) for q in prone_on_hand[(code, ndc)][-STOCK_HISTORY_DAYS:]]
        elif (code, ndc) in consumed:
            tail = consumed[(code, ndc)][-STOCK_HISTORY_DAYS:]
            mean_daily = max(float(np.mean(tail)), 0.05)
            order_up_to = max(round(mean_daily * REORDER_COVER_S), 1)
            reorder_at = max(round(mean_daily * REORDER_POINT_s), 1)
            rng = _rng("stock-history", code, ndc)
            walk = [end_qty]
            for day_qty in reversed(tail[1:]):
                prev = walk[-1] + day_qty
                if prev > order_up_to:  # a delivery landed that morning
                    prev = max(round(reorder_at * rng.uniform(0.3, 0.95)), 0)
                walk.append(prev)
            qtys = list(reversed(walk))
        else:
            qtys = [end_qty] * STOCK_HISTORY_DAYS  # partner: static assortment
        rows.extend(
            (code, ndc, dates[i], qtys[i]) for i in range(min(len(qtys), STOCK_HISTORY_DAYS))
        )
    return rows


def gen_stock(drugs: list[dict], end_stock: dict[tuple[str, str], int]) -> list:
    """Stock per facility/location/NDC. Operated sites carry the consumption
    sim's ending balance (the consistency invariant tests pin); partners get a
    static assortment for the shortage matrix."""
    rows: list = []
    for facility in FACILITIES:
        code = facility["code"]
        for drug in drugs:
            location = location_for(code, drug["storage_class"])
            if location is None:
                continue
            if code == MISPLACED["facility"] and drug["query_name"] == MISPLACED["query_name"]:
                location = "main-room"  # planted: cold-chain drug on a room shelf
            if facility["scale"] is not None:
                qty = end_stock[(code, drug["ndc"])]
            else:
                rng = _rng("partner-stock", code, drug["ndc"])
                if rng.random() < 0.35:  # partners stock a subset
                    continue
                qty = round(drug["base_daily"] * rng.uniform(0.5, 5.0))
            rows.append((code, location, drug["ndc"], qty))
    return rows


def _condition_series(kind: str, facility_code: str, location_code: str) -> tuple:
    """Hourly (temperature, humidity) for one location over CONDITION_DAYS."""
    n = CONDITION_DAYS * 24
    ts = np.datetime64(CONDITION_END.replace(tzinfo=None)) - np.arange(n - 1, -1, -1).astype(
        "timedelta64[h]"
    )
    hour = (ts.astype("datetime64[h]") - ts.astype("datetime64[D]")).astype(int)
    day_cycle = np.cos(2.0 * np.pi * (hour - 15) / 24.0)  # warmest ~15:00
    rng = _rng("conditions", facility_code, location_code)

    params = {
        "room": (21.3, 1.2, 0.35, 46.0, 4.0, 2.0),
        "fridge": (4.6, 0.3, 0.5, 55.0, 3.0, 2.5),
        "freezer": (-19.5, 0.5, 0.7, 62.0, 3.0, 3.0),
        "cold_room": (5.0, 0.4, 0.4, 58.0, 3.0, 2.0),
    }
    t_base, t_amp, t_noise, h_base, h_amp, h_noise = params[kind]
    temp = t_base + t_amp * day_cycle + rng.normal(0.0, t_noise, n)
    hum = h_base + h_amp * day_cycle + rng.normal(0.0, h_noise, n)

    if facility_code == "warehouse-north" and location_code == "bulk-room":
        # No AC: summer coupling to outdoors + the July heatwave afternoons.
        doy = (ts.astype("datetime64[D]") - ts.astype("datetime64[Y]")).astype(int) + 1
        summer = ((1.0 + np.cos(2.0 * np.pi * (doy - 201) / 365.25)) / 2.0) ** 2
        # Tuned so ordinary summer afternoons graze ~25 °C and only the
        # heatwave week clearly breaches CRT limits.
        temp = temp + 1.8 * summer + 0.7 * summer * day_cycle
        in_heatwave = (ts >= HEATWAVE_START) & (ts < HEATWAVE_END)
        afternoon = (hour >= 12) & (hour <= 18)
        temp = temp + np.where(in_heatwave & afternoon, 3.5, 0.0)

    if facility_code == "central" and location_code == "fridge-1":
        # Compressor failure: ramps toward ~14 °C overnight, repaired by 08:00.
        fail = (ts >= FRIDGE_FAIL_START) & (ts < FRIDGE_FAIL_END)
        ramp = np.cumsum(fail).astype(float)
        ramp = ramp / max(ramp.max(), 1.0)
        temp = np.where(fail, 4.5 + 9.7 * ramp + rng.normal(0.0, 0.3, n), temp)

    if facility_code == "westend" and location_code == "main-room":
        breach = (ts >= HUMIDITY_BREACH_START) & (ts < HUMIDITY_BREACH_END)
        hum = hum + np.where(breach, 20.0, 0.0)

    return ts, temp, hum


def gen_conditions() -> list:
    rows: list = []
    for facility in FACILITIES:
        if not facility["operated"]:
            continue
        for code, _name, kind in LOCATIONS[facility["code"]]:
            ts, temp, hum = _condition_series(kind, facility["code"], code)
            ts_iso = ts.astype("datetime64[s]")
            for i in range(len(ts)):
                rows.append(
                    (
                        facility["code"],
                        code,
                        f"{ts_iso[i]}+00:00",
                        f"{temp[i]:.2f}",
                        f"{hum[i]:.2f}",
                    )
                )
    return rows


def run() -> dict[str, int]:
    out = data_dir()
    drugs = load_drugs(out / "drugs.csv")
    consumption, end_stock, prone_on_hand = gen_consumption(drugs)
    stock = gen_stock(drugs, end_stock)
    conditions = gen_conditions()
    forecast = gen_forecast(consumption)
    stock_history = gen_stock_history(stock, consumption, prone_on_hand, drugs)
    counts = {
        "consumption": _write_gz(
            out / "consumption.csv.gz",
            ["facility", "ndc", "rxcui", "date", "qty", "stockout"],
            consumption,
        ),
        "stock": _write_gz(out / "stock.csv.gz", ["facility", "location", "ndc", "qty"], stock),
        "conditions": _write_gz(
            out / "conditions.csv.gz",
            ["facility", "location", "ts", "temperature_c", "humidity_pct"],
            conditions,
        ),
        "forecast": _write_gz(
            out / "forecast.csv.gz",
            ["facility", "ndc", "date", "p10", "p50", "p90"],
            forecast,
        ),
        "stock_history": _write_gz(
            out / "stock_history.csv.gz",
            ["facility", "ndc", "date", "qty"],
            stock_history,
        ),
    }
    return counts


if __name__ == "__main__":
    for name, count in run().items():
        print(f"gen_demo: {name}: {count} rows")
