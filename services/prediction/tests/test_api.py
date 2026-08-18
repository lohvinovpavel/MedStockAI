"""Prediction API against the real schema (CI migrates, then runs these).

Fixture rows carry their own throwaway hospital so runs never collide with
seeded demo data. Reads follow the repo's as-if-RLS convention (no
hand-written hospital predicates — docs/services.md §8), so assertions are
scoped through the test facility and test NDCs rather than expecting
cross-tenant emptiness.
"""

import uuid
from datetime import date, timedelta

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import Principal, current_principal
from medstock_shared.db import engine
from medstock_shared.models import (
    ConsumptionDaily,
    Drug,
    Facility,
    ForecastPoint,
    Hospital,
    ShortageEvent,
    StockSnapshot,
)
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

HOSPITAL_ID = uuid.UUID("00000000-0000-0000-0000-00000000f0de")
END = date(2026, 8, 14)
NDC_FLAT = "88888-test-01"  # 60 days flat 10/day, 100 on hand → 10 days
RX_FLAT = "888801"
NDC_SHORT = "88888-test-02"  # 10 days history → no forecast rows
RX_SHORT = "888802"
NDC_DRY = "88888-test-03"  # in shortage, flat 5/day, 25 on hand → 5 days
RX_DRY = "888803"


@pytest.fixture(scope="module")
def seeded():
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_ID, name="TEST HOSPITAL F"))
        s.flush()
        fac = Facility(
            hospital_id=HOSPITAL_ID, code="tf-central", name="Test Central F", type="Hospital",
            operated=True,
        )
        s.add(fac)
        s.flush()
        for ndc, name in [
            (NDC_FLAT, "Testflat 10 MG"),
            (NDC_SHORT, "Testshort 20 MG"),
            (NDC_DRY, "Testdry 30 MG"),
        ]:
            s.merge(Drug(ndc=ndc, name=name, raw={"source": "test"}))
        spec = [
            (NDC_FLAT, RX_FLAT, 60, 10, 100),
            (NDC_SHORT, RX_SHORT, 10, 4, 200),
            (NDC_DRY, RX_DRY, 60, 5, 25),
        ]
        for ndc, rxcui, days, qty, on_hand in spec:
            s.add_all(
                ConsumptionDaily(
                    hospital_id=str(HOSPITAL_ID), facility_id=fac.id, ndc=ndc, rxcui=rxcui,
                    date=END - timedelta(days=i), qty_consumed=qty, stockout=False,
                )
                for i in range(days)
            )
            s.add(
                StockSnapshot(
                    hospital_id=str(HOSPITAL_ID), ndc=ndc, facility_id=fac.id,
                    location_id="tf-room", quantity=on_hand,
                )
            )
        s.merge(
            ShortageEvent(source_id=f"test-shortage-{NDC_DRY}", ndc=NDC_DRY, status="Current",
                          raw={"source": "test"})
        )
        s.commit()
        fac_id = fac.id

    yield {"facility": fac_id}

    with Session(engine) as s:
        for model, col in [
            (ConsumptionDaily, ConsumptionDaily.hospital_id),
            (StockSnapshot, StockSnapshot.hospital_id),
            (ForecastPoint, ForecastPoint.hospital_id),
        ]:
            s.execute(delete(model).where(col == str(HOSPITAL_ID)))
        s.execute(delete(ShortageEvent).where(ShortageEvent.source_id.like("test-shortage-%")))
        s.execute(delete(Drug).where(Drug.ndc.in_([NDC_FLAT, NDC_SHORT, NDC_DRY])))
        s.execute(delete(Facility).where(Facility.hospital_id == HOSPITAL_ID))
        s.execute(delete(Hospital).where(Hospital.id == HOSPITAL_ID))
        s.commit()


def _client(role: str = "pharmacist") -> TestClient:
    app.dependency_overrides[current_principal] = lambda: Principal(
        "user-tf", str(HOSPITAL_ID), role
    )
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def run(seeded):
    """One posted run for the test hospital; module-scoped so read tests
    share it. as_of pins the cutoff to the fixture's END date."""
    body = _client().post("/forecast/runs", json={"as_of": END.isoformat()}).json()
    return {**body, **seeded}


# --- auth -------------------------------------------------------------------


def test_forecast_requires_auth():
    assert TestClient(app).get(f"/forecast/{RX_FLAT}").status_code == 401


@pytest.mark.parametrize("role", ["physician", "admin", "ghost"])
def test_forecast_read_is_pharmacist_and_director_only(role):
    assert _client(role=role).get(f"/forecast/{RX_FLAT}").status_code == 403


@pytest.mark.parametrize("role", ["physician", "admin"])
def test_run_trigger_excludes_other_roles(role):
    assert _client(role=role).post("/forecast/runs").status_code == 403


def test_director_may_read(run):
    assert _client(role="director").get(f"/forecast/{RX_FLAT}").status_code == 200


# --- the run ----------------------------------------------------------------


def test_run_reports_and_skips_short_history(run):
    assert run["points_written"] > 0
    assert run["skus_skipped"] >= 1  # NDC_SHORT has 10 days
    assert run["data_through"] == END.isoformat()


def test_rerun_same_day_replaces(run):
    first = run["run_id"]
    second = _client().post("/forecast/runs", json={"as_of": END.isoformat()}).json()
    assert second["run_id"] != first
    with Session(engine) as s:
        today_runs = s.execute(
            select(func.count(func.distinct(ForecastPoint.run_id))).where(
                ForecastPoint.hospital_id == str(HOSPITAL_ID),
                func.date(ForecastPoint.created_at) == func.current_date(),
            )
        ).scalar()
    assert today_runs == 1


def test_quantiles_ordered_for_every_row(run):
    with Session(engine) as s:
        bad = s.execute(
            select(func.count()).select_from(ForecastPoint).where(
                ForecastPoint.hospital_id == str(HOSPITAL_ID),
                (ForecastPoint.p10 > ForecastPoint.p50) | (ForecastPoint.p50 > ForecastPoint.p90),
            )
        ).scalar()
    assert bad == 0


# --- GET /forecast ----------------------------------------------------------


def test_forecast_flat_drug(run):
    body = _client().get(
        f"/forecast/{RX_FLAT}", params={"facility_id": run["facility"], "horizon_days": 30}
    ).json()
    assert len(body["forecast"]) == 30
    assert body["history"], "history series must be returned"
    assert all(p["p10"] <= p["p50"] <= p["p90"] for p in body["forecast"])
    # 100 on hand at 10/day: the depletion line lands 10 days out.
    assert body["depletion"]["days"] == 10
    assert body["depletion"]["basis"] == "p50"
    assert body["data_through"] == END.isoformat()
    assert body["model_version"] == "seasonal_naive_quantile-1"


def test_forecast_short_history_says_so(run):
    # E1: no rows, a reason, HTTP 200 — and E2's trailing-mean fallback still
    # gives the depletion a number.
    body = _client().get(f"/forecast/{RX_SHORT}", params={"facility_id": run["facility"]})
    assert body.status_code == 200
    body = body.json()
    assert body["forecast"] == []
    assert body["reason"] == "insufficient_history"
    assert body["depletion"]["basis"] == "trailing_mean"
    assert body["depletion"]["days"] == 50  # 200 on hand / 4 a day


def test_forecast_unknown_rxcui_is_200_no_history(run):
    body = _client().get("/forecast/00000000")
    assert body.status_code == 200
    assert body.json()["reason"] == "no_history"
    assert body.json()["forecast"] == []


# --- surge (E3) -------------------------------------------------------------


def test_surge_100_identical_to_omitted(run):
    params = {"facility_id": run["facility"]}
    plain = _client().get(f"/forecast/{RX_FLAT}", params=params)
    at100 = _client().get(f"/forecast/{RX_FLAT}", params={**params, "surge_pct": 100})
    assert plain.content == at100.content


def test_surge_shrinks_depletion_and_reports_baseline(run):
    params = {"facility_id": run["facility"]}
    base = _client().get(f"/forecast/{RX_FLAT}", params=params).json()
    surged = _client().get(f"/forecast/{RX_FLAT}", params={**params, "surge_pct": 300}).json()
    assert surged["scenario"] == "surge"
    assert surged["depletion"]["days"] < base["depletion"]["days"]
    assert surged["baseline_depletion"]["days"] == base["depletion"]["days"]
    assert surged["forecast"][0]["p50"] == pytest.approx(base["forecast"][0]["p50"] * 3)


@pytest.mark.parametrize("surge", [0, 99, 301])
def test_surge_out_of_range_is_422(run, surge):
    assert _client().get(f"/forecast/{RX_FLAT}", params={"surge_pct": surge}).status_code == 422


# --- at-risk (E2) -----------------------------------------------------------


def test_at_risk_sorted_worst_first_with_shortage_flag(run):
    body = _client().get(
        "/at-risk", params={"facility_id": run["facility"], "within_days": 30}
    ).json()
    mine = [i for i in body["items"] if i["ndc"].startswith("88888-test-")]
    by_ndc = {i["ndc"]: i for i in mine}
    # NDC_DRY (5 days) sorts before NDC_FLAT (10 days).
    assert [i["ndc"] for i in mine[:2]] == [NDC_DRY, NDC_FLAT]
    assert by_ndc[NDC_DRY]["in_shortage"] is True
    assert by_ndc[NDC_FLAT]["in_shortage"] is False
    assert all(i["reorder_point"] is None for i in mine)
    days_list = [i["days_of_supply"] for i in body["items"]]
    assert days_list == sorted(days_list)
    for i in mine:
        if i["days_of_supply_p90"] is not None:
            assert i["days_of_supply_p90"] <= i["days_of_supply"]


def test_at_risk_within_days_filters(run):
    body = _client().get(
        "/at-risk", params={"facility_id": run["facility"], "within_days": 7}
    ).json()
    mine = {i["ndc"] for i in body["items"] if i["ndc"].startswith("88888-test-")}
    assert NDC_DRY in mine
    assert NDC_FLAT not in mine  # 10 days > 7


# --- single SKU -------------------------------------------------------------


def test_days_of_supply_single_sku(run):
    body = _client().get(
        "/days-of-supply", params={"ndc": NDC_DRY, "facility_id": run["facility"]}
    ).json()
    assert body["days_of_supply"] == 5
    assert body["in_shortage"] is True
    assert body["run_id"] == _latest_test_run()


def _latest_test_run() -> str:
    with Session(engine) as s:
        return s.execute(
            select(ForecastPoint.run_id)
            .order_by(ForecastPoint.created_at.desc())
            .limit(1)
        ).scalar()


# --- ruleset ----------------------------------------------------------------


def test_ruleset_publishes_assumptions(run):
    body = _client().get("/ruleset").json()
    assert body["model_version"] == "seasonal_naive_quantile-1"
    assert body["horizon_days"] == 90
    assert body["surge_pct_bounds"] == [100, 300]
    assert body["notes"]
