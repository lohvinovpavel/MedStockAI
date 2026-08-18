"""C5 local availability overlay — ranking unchanged, stock is an annotation."""

from types import SimpleNamespace

from app.availability import overlay_availability
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import Principal, current_principal
from sqlalchemy.exc import SQLAlchemyError

PHARMACIST = Principal("user-1", "hospital-1", "pharmacist")


def _client() -> TestClient:
    app.dependency_overrides[current_principal] = lambda: PHARMACIST
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _fac(fid: int, name: str, lat: float, lon: float, operated: bool = True):
    return SimpleNamespace(id=fid, name=name, lat=lat, lon=lon, operated=operated)


def test_overlay_does_not_reorder_and_nearest_is_not_origin():
    items = [
        {"rxcui": "a", "name": "first", "quantity": 10, "in_stock": True, "stock_status": "low"},
        {"rxcui": "b", "name": "second", "quantity": 0, "in_stock": False, "stock_status": "none"},
    ]
    origin = _fac(1, "Central Hospital", 50.45, 30.523)
    warehouse = _fac(4, "Regional Warehouse North", 50.7554, 30.523)
    partner = _fac(5, "St. Luke Hospital", 50.45, 30.6922, operated=False)
    qty = {
        (1, "ndc-a"): 0,
        (4, "ndc-a"): 1200,
        (5, "ndc-a"): 210,
        (4, "ndc-b"): 40,
    }
    out = overlay_availability(
        items,
        {"a": ["ndc-a"], "b": ["ndc-b"]},
        qty,
        [origin, warehouse, partner],
        origin,
        operated_only=False,
    )
    assert [row["rxcui"] for row in out] == ["a", "b"]
    assert out[0]["availability"]["facility_id"] == 1
    assert out[0]["availability"]["quantity"] == 0
    nearest = out[0]["availability"]["nearest_with_stock"]
    assert nearest["facility_id"] != 1
    assert nearest["facility_id"] == 5  # 12 km vs warehouse 34 km
    assert nearest["name"] == "St. Luke Hospital"
    assert out[1]["availability"]["quantity"] == 0
    assert out[1]["availability"]["nearest_with_stock"]["facility_id"] == 4


def test_operated_only_excludes_partner():
    items = [{"rxcui": "a", "name": "first", "quantity": 0, "in_stock": False, "stock_status": "none"}]
    origin = _fac(1, "Central Hospital", 50.45, 30.523)
    warehouse = _fac(4, "Regional Warehouse North", 50.7554, 30.523)
    partner = _fac(5, "St. Luke Hospital", 50.45, 30.6922, operated=False)
    qty = {(5, "ndc-a"): 210, (4, "ndc-a"): 48}
    out = overlay_availability(
        items, {"a": ["ndc-a"]}, qty, [origin, warehouse, partner], origin, operated_only=True
    )
    assert out[0]["availability"]["nearest_with_stock"]["facility_id"] == 4


def _candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.related_scd_sbd",
        lambda rxcui: [
            {"rxcui": "high-1", "tty": "SCD", "name": "aspirin 100 MG Oral Tablet"},
            {"rxcui": "none-1", "tty": "SCD", "name": "aspirin chewable tablet"},
        ],
    )
    ndcs = {"high-1": ["ndc-high"], "none-1": ["ndc-none"]}
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: ndcs.get(rxcui, []))
    monkeypatch.setattr(
        "app.main.stock_totals_by_ndc",
        lambda principal, found: {"ndc-high": 150},
    )


def test_without_facility_id_response_has_no_availability(monkeypatch):
    _candidates(monkeypatch)
    body = _client().get("/analogues/212033").json()
    assert "availability" not in body["items"][0]
    assert "stock_degraded" not in body
    assert [row["rxcui"] for row in body["items"]] == ["high-1", "none-1"]


def test_facility_id_overlays_without_changing_rank(monkeypatch):
    _candidates(monkeypatch)
    origin = _fac(1, "Central Hospital", 50.45, 30.523)
    other = _fac(4, "Regional Warehouse North", 50.7554, 30.523)
    monkeypatch.setattr("app.main.load_facilities", lambda principal: [origin, other])
    calls = []

    def stock_query(principal, ndcs):
        calls.append(list(ndcs))
        return {(1, "ndc-high"): 12, (4, "ndc-high"): 90, (4, "ndc-none"): 5}

    monkeypatch.setattr("app.main.stock_qty_by_facility_ndc", stock_query)
    body = _client().get("/analogues/212033", params={"facility_id": 1}).json()
    assert [row["rxcui"] for row in body["items"]] == ["high-1", "none-1"]
    assert body["items"][0]["availability"]["quantity"] == 12
    assert body["items"][0]["availability"]["nearest_with_stock"]["facility_id"] == 4
    assert body["items"][1]["availability"]["quantity"] == 0
    assert len(calls) == 1


def test_switching_facility_flips_stocked(monkeypatch):
    _candidates(monkeypatch)
    central = _fac(1, "Central Hospital", 50.45, 30.523)
    riverside = _fac(2, "Riverside Outpatient", 50.6207, 30.523)
    monkeypatch.setattr("app.main.load_facilities", lambda principal: [central, riverside])
    monkeypatch.setattr(
        "app.main.stock_qty_by_facility_ndc",
        lambda principal, ndcs: {(1, "ndc-high"): 12},
    )
    here = _client().get("/analogues/212033", params={"facility_id": 1}).json()
    away = _client().get("/analogues/212033", params={"facility_id": 2}).json()
    assert here["items"][0]["availability"]["quantity"] == 12
    assert away["items"][0]["availability"]["quantity"] == 0
    assert away["items"][0]["availability"]["nearest_with_stock"]["facility_id"] == 1


def test_stock_failure_degrades(monkeypatch):
    _candidates(monkeypatch)
    origin = _fac(1, "Central Hospital", 50.45, 30.523)
    monkeypatch.setattr("app.main.load_facilities", lambda principal: [origin])

    def boom(principal, ndcs):
        raise SQLAlchemyError("stock_snapshot unavailable")

    monkeypatch.setattr("app.main.stock_qty_by_facility_ndc", boom)
    body = _client().get("/analogues/212033", params={"facility_id": 1}).json()
    assert body["stock_degraded"] is True
    assert [row["rxcui"] for row in body["items"]] == ["high-1", "none-1"]
    assert body["items"][0]["availability"] is None


def test_unknown_facility_is_404(monkeypatch):
    _candidates(monkeypatch)
    monkeypatch.setattr("app.main.load_facilities", lambda principal: [])
    res = _client().get("/analogues/212033", params={"facility_id": 99})
    assert res.status_code == 404
